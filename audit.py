#!/usr/bin/env python3
"""
Bank statement audit: cross-reference bank transactions with Paperless-ngx documents.

Usage: python audit.py YYYY MM
"""

import json
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import openai
import openpyxl
import requests
import xlrd
from dotenv import load_dotenv
import os

load_dotenv()

PAPERLESS_URL = os.environ["PAPERLESS_URL"].rstrip("/")
PAPERLESS_TOKEN = os.environ["PAPERLESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
BANK_STATEMENT_TAG_ID = int(os.environ["BANK_STATEMENT_TAG_ID"])
WRITE_NOTES = os.environ.get("WRITE_NOTES", "false").lower() in ("true", "1", "yes")

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

HEADERS = {"Authorization": f"Token {PAPERLESS_TOKEN}"}


# ── Paperless API ─────────────────────────────────────────────────────

def paperless_get(endpoint, params=None):
    resp = requests.get(f"{PAPERLESS_URL}{endpoint}", headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()


def get_statements(tag_id, year, month):
    """Fetch bank statement documents for the given month."""
    first_day = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)

    docs = []
    params = {
        "tags__id__all": tag_id,
        "created__date__gte": first_day.isoformat(),
        "created__date__lt": next_month.isoformat(),
        "page_size": 100,
    }
    data = paperless_get("/api/documents/", params)
    docs.extend(data.get("results", []))

    while data.get("next"):
        data = requests.get(data["next"], headers=HEADERS).json()
        docs.extend(data.get("results", []))

    return docs


def download_original(doc_id):
    """Download the original file from Paperless. Returns (bytes, filename)."""
    resp = requests.get(
        f"{PAPERLESS_URL}/api/documents/{doc_id}/download/",
        headers=HEADERS,
        params={"original": "true"},
    )
    resp.raise_for_status()
    # Extract filename from Content-Disposition header
    cd = resp.headers.get("Content-Disposition", "")
    filename = ""
    if "filename=" in cd:
        match = re.search(r'filename="([^"]+)"', cd)
        if match:
            filename = match.group(1)
    return resp.content, filename


def get_document_content(doc_id):
    """Get the full text content of a document."""
    doc = paperless_get(f"/api/documents/{doc_id}/")
    return doc.get("content", "")


def search_documents(query, date_from=None, date_to=None):
    """Search Paperless for documents matching a query."""
    params = {"query": query, "page_size": 10}
    if date_from:
        params["created__date__gte"] = date_from
    if date_to:
        params["created__date__lte"] = date_to
    data = paperless_get("/api/documents/", params)
    return data.get("results", [])


# ── XLS parsing ───────────────────────────────────────────────────────

def parse_xls_to_text(file_bytes, filename):
    """Parse XLS/XLSX file into structured text for OpenAI."""
    suffix = Path(filename).suffix.lower() if filename else ""
    if suffix not in (".xls", ".xlsx"):
        return None

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(file_bytes)
        tmp_path = f.name

    try:
        if suffix == ".xls":
            return _parse_xls_legacy(tmp_path)
        else:
            return _parse_xlsx(tmp_path)
    except Exception:
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _parse_xls_legacy(path):
    """Parse old-format .xls (BIFF) using xlrd."""
    wb = xlrd.open_workbook(path)
    lines = []
    for sheet in wb.sheets():
        for row_idx in range(sheet.nrows):
            cells = [str(sheet.cell_value(row_idx, col)) for col in range(sheet.ncols)]
            if not any(cells):
                continue
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _parse_xlsx(path):
    """Parse .xlsx using openpyxl."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines = []
    for sheet in wb.worksheets:
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if not any(cells):
                continue
            lines.append(" | ".join(cells))
    wb.close()
    return "\n".join(lines)


# ── OpenAI ────────────────────────────────────────────────────────────

def extract_transactions(statement_text):
    """Send statement text to OpenAI, get structured transaction list."""
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a bank statement parser. Extract all transactions "
                    "from the provided bank statement text. Return a JSON array "
                    "of objects with these fields:\n"
                    '- "date": transaction date in YYYY-MM-DD format\n'
                    '- "amount": transaction amount as a number (positive for '
                    "credits, negative for debits)\n"
                    '- "counterparty": name of the other party\n'
                    '- "description": payment description/reference\n'
                    '- "ref": invoice or document reference number if mentioned '
                    "(e.g. invoice number, contract number), otherwise empty string\n\n"
                    "Return ONLY the JSON array, no other text."
                ),
            },
            {"role": "user", "content": statement_text},
        ],
        temperature=0,
    )

    text = response.choices[0].message.content.strip()
    # Strip markdown code fence if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    return json.loads(text)


# ── Cache ─────────────────────────────────────────────────────────────

def cache_path(year, month):
    return CACHE_DIR / f"{year}-{month:02d}.json"


def load_cache(year, month):
    path = cache_path(year, month)
    if path.exists():
        return json.loads(path.read_text())
    return {"statements": {}}


def save_cache(year, month, data):
    path = cache_path(year, month)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ── Matching ──────────────────────────────────────────────────────────

def extract_refs(tx):
    """Extract searchable references from transaction description and ref field."""
    refs = []
    ref = tx.get("ref", "")
    if ref:
        refs.append(ref)

    desc = tx.get("description", "")
    # Look for invoice-like patterns: DH-202512-10218, INV-2025-042, Nr. 123, etc.
    patterns = [
        r"[A-Z]{2,}-\d[\d-]+\d",       # DH-202512-10218, INV-2025-042
        r"[Nn]r\.?\s*(\S+)",            # Nr. 12345 or nr 12345
        r"[Rr]ēķin\S*\s+\S*\s*(\S+)",  # rēķins/rēķinu Nr ...
    ]
    for pat in patterns:
        for m in re.finditer(pat, desc):
            refs.append(m.group(0) if not m.groups() else m.group(1))

    return refs


def find_non_statement(results):
    """Return first result that is not a bank statement."""
    for doc in results:
        if BANK_STATEMENT_TAG_ID in doc.get("tags", []):
            continue
        return doc
    return None


def match_transaction(tx):
    """Try to find a document in Paperless that matches this transaction."""
    tx_date = tx["date"]
    counterparty = tx["counterparty"]
    abs_amount = f"{abs(tx['amount']):.2f}"

    # Credits (incoming payments) are usually for invoices from previous months
    is_credit = tx["amount"] > 0
    lookback = 365 if is_credit else 30

    try:
        d = date.fromisoformat(tx_date)
        date_from = (d - timedelta(days=lookback)).isoformat()
        date_to = (d + timedelta(days=14)).isoformat()
    except ValueError:
        date_from = date_to = None

    # 1. Search by reference numbers (most precise)
    for ref in extract_refs(tx):
        results = search_documents(ref, date_from, date_to)
        doc = find_non_statement(results)
        if doc:
            return doc

    # 2. Search by counterparty + amount
    results = search_documents(f"{counterparty} {abs_amount}", date_from, date_to)
    doc = find_non_statement(results)
    if doc:
        return doc

    # 3. Search by amount only (last resort, narrow date range)
    try:
        d = date.fromisoformat(tx_date)
        narrow_from = (d - timedelta(days=5)).isoformat()
        narrow_to = (d + timedelta(days=5)).isoformat()
    except ValueError:
        narrow_from = narrow_to = None
    results = search_documents(abs_amount, narrow_from, narrow_to)
    doc = find_non_statement(results)
    if doc:
        return doc

    return None


# ── Notes ─────────────────────────────────────────────────────────

AUDIT_NOTE_PREFIX = "[AUDIT]"


def get_existing_audit_note(doc_id):
    """Find an existing audit note on a document. Returns (note_id, text) or (None, None)."""
    data = paperless_get(f"/api/documents/{doc_id}/notes/")
    for note in data:
        if note.get("note", "").startswith(AUDIT_NOTE_PREFIX):
            return note["id"], note["note"]
    return None, None


def format_tx_block(tx, symbol):
    """Format a single transaction as a multi-line block."""
    lines = [f"{symbol} {tx['date']} / {tx['amount']:.2f}"]
    lines.append(tx["counterparty"])
    desc = tx.get("description", "")
    if desc:
        lines.append(desc)
    if tx.get("matched_doc_id"):
        lines.append(f"→ #{tx['matched_doc_id']} {tx.get('matched_title', '')}")
    return "\n".join(lines)


def write_audit_note(doc_id, transactions):
    """Write or update the audit note on a statement document."""
    missing = [tx for tx in transactions if not tx.get("matched_doc_id")]
    matched = [tx for tx in transactions if tx.get("matched_doc_id")]
    total = len(transactions)

    parts = [f"{AUDIT_NOTE_PREFIX} {len(matched)}/{total}"]

    if missing:
        parts.append(f"\n[MISSING][{len(missing)}/{total}]")
        for tx in missing:
            parts.append(format_tx_block(tx, "✗"))

    if matched:
        parts.append(f"\n[MATCHED][{len(matched)}/{total}]")
        for tx in matched:
            parts.append(format_tx_block(tx, "✓"))

    note_text = "\n\n".join(parts)

    # Delete old audit note if exists
    old_id, _ = get_existing_audit_note(doc_id)
    if old_id:
        requests.delete(
            f"{PAPERLESS_URL}/api/documents/{doc_id}/notes/?id={old_id}",
            headers=HEADERS,
        )

    # Create new note
    requests.post(
        f"{PAPERLESS_URL}/api/documents/{doc_id}/notes/",
        headers=HEADERS,
        json={"note": note_text},
    )


# ── Main ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 3:
        print("Usage: python audit.py YYYY MM")
        sys.exit(1)

    year = int(sys.argv[1])
    month = int(sys.argv[2])

    print(f"Auditing bank statements for {year}-{month:02d}")
    print()

    statements = get_statements(BANK_STATEMENT_TAG_ID, year, month)

    if not statements:
        print("No bank statements found for this month.")
        return

    print(f"Found {len(statements)} statement(s)")
    print()

    cache = load_cache(year, month)
    total_matched = 0
    total_unmatched = 0

    for stmt in statements:
        doc_id = str(stmt["id"])
        title = stmt.get("title", f"Document #{doc_id}")
        print(f"── {title} (#{doc_id}) ──")

        # Check if transactions already extracted (cached)
        if doc_id in cache["statements"]:
            transactions = cache["statements"][doc_id]["transactions"]
            print(f"  Using cached transactions ({len(transactions)} items)")
        else:
            # Try to download original and parse as XLS
            print("  Downloading original from Paperless...")
            content = None
            try:
                file_bytes, filename = download_original(stmt["id"])
                content = parse_xls_to_text(file_bytes, filename)
                if content:
                    print(f"  Parsed XLS: {filename}")
            except Exception as e:
                print(f"  Could not download original: {e}")

            # Fallback to OCR text content
            if not content:
                print("  Using OCR text content...")
                content = get_document_content(stmt["id"])

            if not content or not content.strip():
                print("  WARNING: Empty document content, skipping")
                continue

            print("  Extracting transactions via OpenAI...")
            try:
                transactions = extract_transactions(content)
            except Exception as e:
                print(f"  ERROR extracting transactions: {e}")
                continue

            print(f"  Found {len(transactions)} transactions")
            cache["statements"][doc_id] = {"transactions": transactions}

        # Match each transaction
        for tx in transactions:
            # Skip already matched
            if tx.get("matched_doc_id"):
                total_matched += 1
                doc = tx["matched_doc_id"]
                print(f"  ✓ {tx['date']} | {tx['amount']:>10.2f} | {tx['counterparty']:<30} | → #{doc}")
                continue

            # Try to match
            match = match_transaction(tx)
            if match:
                tx["matched_doc_id"] = match["id"]
                tx["matched_title"] = match.get("title", "")
                total_matched += 1
                print(f"  ✓ {tx['date']} | {tx['amount']:>10.2f} | {tx['counterparty']:<30} | → #{match['id']} {match.get('title', '')}")
            else:
                total_unmatched += 1
                print(f"  ✗ {tx['date']} | {tx['amount']:>10.2f} | {tx['counterparty']:<30} | NOT FOUND")

        # Write note to Paperless
        if WRITE_NOTES:
            write_audit_note(stmt["id"], transactions)
            print(f"  📝 Note updated on #{doc_id}")

        print()

    # Save cache
    save_cache(year, month, cache)

    # Summary
    total = total_matched + total_unmatched
    print("═" * 60)
    print(f"Total: {total} transactions")
    print(f"  Matched:   {total_matched}")
    print(f"  Unmatched: {total_unmatched}")
    if total:
        print(f"  Coverage:  {total_matched / total * 100:.0f}%")


if __name__ == "__main__":
    main()
