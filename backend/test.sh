#!/bin/sh
# Group Planning Assistant — one-command test run (roadmap B-005).
# Runs the whole pytest suite and ends with a one-page report:
# counts, pass/fail, and any failure named plainly. This same report
# ships inside every release ZIP.
cd "$(dirname "$0")"

echo "=================================================="
echo " Group Planning Assistant — test report"
echo "=================================================="
uv run pytest -q > /tmp/gpa-test-output.txt 2>&1
STATUS=$?
cat /tmp/gpa-test-output.txt
echo "--------------------------------------------------"
grep -E "passed|failed|error" /tmp/gpa-test-output.txt | tail -1
echo " Full output above; failures, if any, are named there."
echo "=================================================="
exit $STATUS
