#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.." \
    && pwd
)"

PYTHON="$REPO_ROOT/.venv/bin/python"
BASE_URL="http://127.0.0.1:8000"

cd "$REPO_ROOT"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: Python virtual environment not found."
    exit 1
fi


echo "=== 1. PYTHON SYNTAX ==="

"$PYTHON" -m compileall \
    -q \
    webapp \
    tests

echo "PASS: Python syntax"


echo
echo "=== 2. REGRESSION TESTS ==="

"$PYTHON" -m pytest \
    tests/test_webapp_baseline.py \
    -q


echo
echo "=== 3. GIT DIFF CHECK ==="

git diff --check

echo "PASS: git diff check"


echo
echo "=== 4. RESTART FASTAPI ==="

sudo systemctl restart ana-webapp


echo
echo "=== 5. WAIT FOR FASTAPI ==="

ready=0

for attempt in $(seq 1 10)
do
    if curl -fsS \
        "$BASE_URL/health" \
        >/dev/null 2>&1
    then
        echo "PASS: FastAPI ready after attempt $attempt."
        ready=1
        break
    fi

    sleep 1
done

if [[ "$ready" -ne 1 ]]; then
    echo "FAIL: FastAPI did not become ready."
    sudo systemctl status ana-webapp --no-pager
    exit 1
fi


check_route() {
    local label="$1"
    local url="$2"
    local expected="$3"
    local code

    if ! code=$(
        curl -sS \
            -o /dev/null \
            -w '%{http_code}' \
            "$url"
    ); then
        echo "FAIL: $label could not be reached."
        return 1
    fi

    if [[ "$code" != "$expected" ]]; then
        echo \
            "FAIL: $label returned HTTP $code; expected $expected."
        return 1
    fi

    echo "PASS: $label -> HTTP $code"
}


echo
echo "=== 6. LIVE ROUTE SMOKE TESTS ==="

check_route \
    "Health" \
    "$BASE_URL/health" \
    "200"

check_route \
    "Dashboard" \
    "$BASE_URL/" \
    "200"

check_route \
    "Inventory" \
    "$BASE_URL/inventory" \
    "200"

check_route \
    "Inventory API" \
    "$BASE_URL/api/inventory" \
    "200"

check_route \
    "Automation" \
    "$BASE_URL/automation" \
    "200"

check_route \
    "Monitoring Overview" \
    "$BASE_URL/monitoring?view=overview" \
    "200"

check_route \
    "Monitoring Interfaces" \
    "$BASE_URL/monitoring?view=interfaces" \
    "200"

check_route \
    "Monitoring Routing" \
    "$BASE_URL/monitoring?view=routing" \
    "200"

check_route \
    "Monitoring Topology" \
    "$BASE_URL/monitoring?view=topology" \
    "200"

check_route \
    "Changes" \
    "$BASE_URL/changes" \
    "200"

check_route \
    "New Site" \
    "$BASE_URL/changes/new-site" \
    "200"

check_route \
    "New Device" \
    "$BASE_URL/changes/new" \
    "200"

check_route \
    "Unknown Monitoring View" \
    "$BASE_URL/monitoring?view=does-not-exist" \
    "404"

check_route \
    "Unknown Managed Device" \
    "$BASE_URL/changes?device=DOES-NOT-EXIST" \
    "404"


echo
echo "=== RESULT ==="
echo "ALL WEBAPP REGRESSION CHECKS PASSED"
