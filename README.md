# paperless-bank-audit

CLI tool that cross-references bank statement transactions against documents in Paperless-ngx using OpenAI for statement parsing.

## How it works

1. Fetches bank statement documents from Paperless by tag ID and month
2. Sends the statement text to OpenAI to extract a structured list of transactions
3. For each transaction, searches Paperless for a matching document (invoice, receipt, etc.)
4. Prints a report showing which transactions have matching documents and which don't
5. Caches results locally — OpenAI is called once per statement, already matched transactions are skipped on re-runs

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```
PAPERLESS_URL=https://your-paperless-instance.com
PAPERLESS_TOKEN=your-api-token
OPENAI_API_KEY=sk-your-key
BANK_STATEMENT_TAG_ID=42
```

- **PAPERLESS_URL** — your Paperless-ngx instance URL
- **PAPERLESS_TOKEN** — API token (Settings → Auth Tokens in Paperless web UI)
- **OPENAI_API_KEY** — OpenAI API key
- **BANK_STATEMENT_TAG_ID** — numeric ID of the tag used for bank statements (find it in Paperless URL when clicking on the tag, e.g. `/tags/42/`)

## Usage

```bash
python audit.py YYYY MM
```

Example:

```bash
python audit.py 2026 02
```

Output:

```
Auditing bank statements for 2026-02

Found 2 statement(s)

── Swedbank February 2026 (#123) ──
  Extracting transactions via OpenAI...
  Found 15 transactions
  ✓ 2026-02-01 |    -125.00 | SIA Roga                       | → #452 Invoice SIA Roga
  ✗ 2026-02-03 |    -200.00 | Bolt                           | NOT FOUND
  ✓ 2026-02-05 |     -89.50 | Maxima                         | → #460 Receipt Maxima

════════════════════════════════════════════════════════════
Total: 15 transactions
  Matched:   12
  Unmatched: 3
  Coverage:  80%
```

## Re-running

Running the same month again will:
- **Skip OpenAI** — transactions are cached in `cache/YYYY-MM.json`
- **Skip matched** — transactions already linked to a document are not re-checked
- **Re-check unmatched** — searches Paperless again for previously missing documents

To force a full re-parse from OpenAI, delete the cache file:

```bash
rm cache/2026-02.json
```
