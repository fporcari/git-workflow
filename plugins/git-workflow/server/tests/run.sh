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
RUN_BENCH=false
[ "${1:-}" = "--bench" ] && RUN_BENCH=true
TEST_LOG=$(mktemp -t git-workflow-tests.XXXXXX)
DESK=""
UI_HOME=""

cleanup() {
  [ -n "$DESK" ] && kill "$DESK" 2>/dev/null || true
  [ -n "$DESK" ] && wait "$DESK" 2>/dev/null || true
  rm -f "$TEST_LOG"
  [ -n "$UI_HOME" ] && rm -rf "$UI_HOME"
}
trap cleanup EXIT INT TERM

echo "== server (unittest, fixture provider) =="
if python3 -m unittest discover -s tests -t . >"$TEST_LOG" 2>&1; then
  tail -5 "$TEST_LOG"
else
  cat "$TEST_LOG"
  exit 1
fi

echo
echo "== ui (real page, real server, fixture provider) =="
# Throwaway state dir: the analyses are durable across relaunches by design,
# and a state left by the previous run would make the checks lie. The HOME
# goes with it so a fixture desk can never reach the real gh config. The
# morning's preparation runs first, for real, with the fake agent on PATH;
# the desk then opens on filled steps and starts nothing itself.
UI_HOME=$(mktemp -d -t git-workflow-ui-home.XXXXXX)
mkdir "$UI_HOME/bin"
cp tests/fixtures/fake_claude.py "$UI_HOME/bin/claude"
chmod +x "$UI_HOME/bin/claude"
HOME="$UI_HOME" GIT_WORKFLOW_STATE_DIR="$UI_HOME/state" PATH="$UI_HOME/bin:$PATH" \
  python3 tests/seed_ui.py --repo desk-tests/ui --me genro >/dev/null
HOME="$UI_HOME" GIT_WORKFLOW_STATE_DIR="$UI_HOME/state" PATH="$UI_HOME/bin:$PATH" \
  python3 prdesk.py --provider fixture --port "$PORT" --repo desk-tests/ui --me genro \
  --no-prepare --keep-state 2>/dev/null &
DESK=$!
for _ in $(seq 40); do
  curl -sf -m 1 "http://127.0.0.1:$PORT/api/meta" >/dev/null 2>&1 && break
  sleep 0.1
done
node tests/test_ui.mjs "$PORT"

if [ "$RUN_BENCH" = true ]; then
  echo
  echo "== bench: served endpoints =="
  python3 tests/bench.py --http --port "$PORT"
  echo
  echo "== bench: live provider =="
  python3 tests/bench.py
fi
