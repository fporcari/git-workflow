#!/bin/sh
# The desk's test environment: no network, no GitHub, no rate limit.
#
#   server/tests/run.sh              server + UI tests
#   server/tests/run.sh --bench      also the live provider benchmark (needs gh)
#
# The UI tests drive the real static/index.html against a real desk process
# backed by the fixture provider, so what is exercised is the page's own
# render path — not a copy of it.
set -e
cd "$(dirname "$0")/.."
PORT=${DESK_TEST_PORT:-8397}

echo "== server (unittest, fixture provider) =="
python3 -m unittest discover -s tests -t . "$@" 2>&1 | tail -5

echo
echo "== ui (real page, real server, fixture provider) =="
# no --no-prefetch: the UI tests exercise the real boot, gate fill included
python3 prdesk.py --provider fixture --port "$PORT" --repo desk-tests/ui 2>/dev/null &
DESK=$!
trap 'kill $DESK 2>/dev/null || true' EXIT INT TERM
for _ in $(seq 40); do
  curl -sf -m 1 "http://127.0.0.1:$PORT/api/meta" >/dev/null 2>&1 && break
  sleep 0.1
done
node tests/test_ui.mjs "$PORT"

if [ "$1" = "--bench" ]; then
  echo
  echo "== bench: served endpoints =="
  python3 tests/bench.py --http --port "$PORT"
  echo
  echo "== bench: live provider =="
  python3 tests/bench.py
fi
