#!/usr/bin/env python3
"""
Bank statement audit: cross-reference bank transactions with Paperless-ngx documents.

Usage: python audit.py YYYY MM
"""

import json
import sys
from datetime import date
from pathlib import Path

import openai
import requests
from dotenv import load_dotenv
import os

load_dotenv()

PAPERLESS_URL = os.environ["PAPERLESS_URL"].rstrip("/")
PAPERLESS_TOKEN = os.environ["PAPERLESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
BANK_STATEMENT_TAG_ID = int(os.environ["BANK_STATEMENT_TAG_ID"])

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
                    '- "description": payment description/reference\n\n'
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

def match_transaction(tx):
    """Try to find a document in Paperless that matches this transaction."""
    amount = tx["amount"]
    counterparty = tx["counterparty"]
    tx_date = tx["date"]

    # Search by counterparty + amount
    abs_amount = f"{abs(amount):.2f}"
    queries = [
        f"{counterparty} {abs_amount}",
        counterparty,
        abs_amount,
    ]

    # Date range: ±7 days
    try:
        d = date.fromisoformat(tx_date)
        from datetime import timedelta
        date_from = (d - timedelta(days=7)).isoformat()
        date_to = (d + timedelta(days=7)).isoformat()
    except ValueError:
        date_from = date_to = None

    for query in queries:
        results = search_documents(query, date_from, date_to)
        if results:
            # Return best match (first result)
            return results[0]

    return None


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
            # Get content and extract via OpenAI
            print("  Fetching content from Paperless...")
            content = get_document_content(stmt["id"])
            if not content.strip():
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
