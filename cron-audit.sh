#!/bin/bash
# Run bank statement audit for current and previous month
cd "$(dirname "$0")"

CURRENT_YEAR=$(date +%Y)
CURRENT_MONTH=$(date +%-m)

if [ "$CURRENT_MONTH" -eq 1 ]; then
    PREV_YEAR=$((CURRENT_YEAR - 1))
    PREV_MONTH=12
else
    PREV_YEAR=$CURRENT_YEAR
    PREV_MONTH=$((CURRENT_MONTH - 1))
fi

docker compose run --rm audit "$PREV_YEAR" "$PREV_MONTH"
docker compose run --rm audit "$CURRENT_YEAR" "$CURRENT_MONTH"
