# paperless-bank-audit

CLI tool that cross-references bank statement transactions against documents in Paperless-ngx using OpenAI for statement parsing.

## How it works

1. Fetches bank statement documents from Paperless by tag ID and month
2. Sends the statement text to OpenAI to extract a structured list of transactions
3. For each transaction, searches Paperless for a matching document (invoice, receipt, etc.)
4. Prints a report showing which transactions have matching documents and which don't
5. Caches results locally — OpenAI is called once per statement, already matched transactions are skipped on re-runs

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
```

- **PAPERLESS_URL** — your Paperless-ngx instance URL
- **PAPERLESS_TOKEN** — API token (Settings → Auth Tokens in Paperless web UI)
- **OPENAI_API_KEY** — OpenAI API key
- **BANK_STATEMENT_TAG_ID** — numeric ID of the tag used for bank statements (find it in Paperless URL when clicking on the tag, e.g. `/tags/3/`)

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

```
Auditing bank statements for 2026-01

Found 2 statement(s)

── Swedbank January 2026 (#90) ──
  Extracting transactions via OpenAI...
  Found 13 transactions
  ✓ 2026-01-04 |      72.60 | ASTRONIK. SIA                  | → #55 Invoice
  ✗ 2026-01-20 |    -630.00 | Marina Ostanina                | NOT FOUND
  ✓ 2026-01-28 |  -15000.00 | Em stark, sia                  | → #96 Loan agreement

════════════════════════════════════════════════════════════
Total: 13 transactions
  Matched:   11
  Unmatched: 2
  Coverage:  85%
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
