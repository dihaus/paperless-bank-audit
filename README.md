# paperless-bank-audit

CLI tool that cross-references bank statement transactions against documents in Paperless-ngx using OpenAI for statement parsing.

## How it works

1. Fetches bank statement documents from Paperless by tag ID and month
2. Downloads the original file — if it's XLS/XLSX, parses it directly (no OCR needed). Falls back to OCR text for PDFs
3. Sends structured data to OpenAI to extract a list of transactions
4. For each transaction, searches Paperless for a matching document by invoice reference, counterparty, or amount. Bank statements are excluded from matching
5. Credits (incoming payments) are matched against invoices up to 1 year back. Debits are matched within 30 days
6. Optionally writes results as a note on each bank statement in Paperless
7. Caches results locally — OpenAI is called once per statement, already matched transactions are skipped on re-runs

## Configuration

```bash
cp .env.example .env
```

Edit `.env`:

```
PAPERLESS_URL=http://localhost:8001
PAPERLESS_TOKEN=your-api-token
OPENAI_API_KEY=sk-your-key
BANK_STATEMENT_TAG_ID=3
WRITE_NOTES=false
```

- **PAPERLESS_URL** — your Paperless-ngx instance URL
- **PAPERLESS_TOKEN** — API token (Settings → Auth Tokens in Paperless web UI)
- **OPENAI_API_KEY** — OpenAI API key
- **BANK_STATEMENT_TAG_ID** — numeric ID of the tag used for bank statements (find it in Paperless URL when clicking on the tag, e.g. `/tags/3/`)
- **WRITE_NOTES** — set to `true` to write audit results as a note on each bank statement in Paperless (default: `false`)

## Usage

### Local (uv)

```bash
uv sync
uv run python audit.py 2025 1
```

### Docker

```bash
docker compose run --rm audit 2025 1
```

## Output

Console:

```
── Bank Statement January 2025 (#12) ──
  Downloading original from Paperless...
  Parsed XLS: statement_january_2025.xls
  Extracting transactions via OpenAI...
  Found 8 transactions
  ✗ 2025-01-05 |     -45.00 | ACME HOSTING                   | NOT FOUND
  ✓ 2025-01-10 |    -250.00 | Office Supplies Ltd            | → #34 Invoice INV-2025-042
  ✓ 2025-01-15 |     120.00 | Client ABC                     | → #37 INV-2024-198
  📝 Note updated on #12

════════════════════════════════════════════════════════════
Total: 8 transactions
  Matched:   6
  Unmatched: 2
  Coverage:  75%
```

Paperless note (when `WRITE_NOTES=true`):

```
[AUDIT] 6/8

[MISSING][2/8]

✗ 2025-01-05 / -45.00
ACME HOSTING
Monthly server fee

✗ 2025-01-22 / -19.99
Cloud IDE Subscription

[MATCHED][6/8]

✓ 2025-01-10 / -250.00
Office Supplies Ltd
Invoice #INV-2025-042
→ #34 Office Supplies Invoice
```

## Matching strategy

1. **By reference** — extracts invoice/document numbers from transaction description and searches Paperless (most precise)
2. **By counterparty + amount** — searches for documents matching both the counterparty name and exact amount
3. **By amount only** — last resort, narrow date range (±5 days)

## Scheduled runs (cron)

`cron-audit.sh` runs the audit for the current and previous month. Set it up with cron:

```bash
crontab -e
```

Add (runs at 9:00 and 18:00 daily):

```
0 9,18 * * * /path/to/paperless-bank-audit/cron-audit.sh >> /path/to/paperless-bank-audit/cache/cron.log 2>&1
```

Log output goes to `cache/cron.log`.

## Re-running

Running the same month again will:
- **Skip OpenAI** — transactions are cached in `cache/YYYY-MM.json`
- **Skip matched** — transactions already linked to a document are not re-checked
- **Re-check unmatched** — searches Paperless again for previously missing documents

To force a full re-parse from OpenAI, delete the cache file:

```bash
rm cache/2025-01.json
```
