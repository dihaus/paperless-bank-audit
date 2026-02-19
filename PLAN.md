# paperless-bank-audit — Plan

## Phase 1 (current): CLI script
- `python audit.py 2026 02` — manual run, month+year
- Console output: matched/unmatched transactions
- Cache in `cache/YYYY-MM.json` — already matched transactions skip re-matching
- OpenAI called once per statement, results cached

## Phase 2: Notes integration
- After audit, write results as a note on the bank statement document in Paperless
- Format: structured list with ✓/✗ and doc IDs
- On re-run, read existing note, only re-check ✗ items
- Remove local cache files, Paperless becomes the source of truth

## Phase 3: Post-consume hook
- Trigger on new document with tag "bankas izraksts" → run audit for that month
- Trigger on new invoice/receipt → re-check unmatched transactions in recent statements
- Configure via `PAPERLESS_POST_CONSUME_SCRIPT` or a custom workflow
- Notifications (email/Telegram) when all transactions for a month are covered
