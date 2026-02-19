# paperless-bank-audit

CLI tool that cross-references bank statement transactions against documents in Paperless-ngx using OpenAI for statement parsing.

## How it works

1. Fetches bank statement documents from Paperless by tag ID and month
2. Sends the statement text to OpenAI to extract a structured list of transactions
3. For each transaction, searches Paperless for a matching document (invoice, receipt, etc.) — bank statements are excluded from matching
4. Prints a report showing which transactions have matching documents and which don't
5. Optionally writes results as a note on each bank statement in Paperless
6. Caches results locally — OpenAI is called once per statement, already matched transactions are skipped on re-runs

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
uv run python audit.py 2026 1
```

### Docker

```bash
docker compose run --rm audit 2026 1
```

## Output

Console:

```
── Bank Statement January 2025 (#12) ──
  Found 8 transactions
  ✗ 2025-01-05 |     -45.00 | ACME HOSTING                   | NOT FOUND
  ✓ 2025-01-10 |    -250.00 | Office Supplies Ltd            | → #34
  ✓ 2025-01-15 |     120.00 | Client ABC                     | → #37
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

## Re-running

Running the same month again will:
- **Skip OpenAI** — transactions are cached in `cache/YYYY-MM.json`
- **Skip matched** — transactions already linked to a document are not re-checked
- **Re-check unmatched** — searches Paperless again for previously missing documents

To force a full re-parse from OpenAI, delete the cache file:

```bash
rm cache/2026-01.json
```
