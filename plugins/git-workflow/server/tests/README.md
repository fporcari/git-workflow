# The desk's test environment

No network, no GitHub, no rate limit — a recorded payload replayed by the
`fixture` provider.

```sh
server/tests/run.sh              # server tests + UI tests
server/tests/run.sh --bench      # plus the live benchmark (needs gh)
```

- **`test_desk.py`** (stdlib unittest) — the row contract the
  skills read, the verdict engine, the merge gate, the five-block partition,
  the chase grouping, the issue cross-check, the cache's
  stale-while-revalidate and single-flight behaviour, every HTTP endpoint
  including the 304 path, and the guarantee that a cold snapshot pays its
  cache misses in parallel rather than one after the other.
- **`test_ui.mjs`** (plain node) — drives the **real**
  `static/index.html` against a **real** desk process on the fixture
  provider, through a small DOM shim. It is the page's own render path that
  runs, so it catches a render that throws, a missing field, a button wired
  to nothing, a detail tab that comes up blank.
- **`bench.py`** — where the time goes. `--queries` is the interesting one:
  it shows that the search itself is cheap, that resolving the per-PR
  nested connections is what costs, and that `mergeStateStatus` alone costs
  more than the whole rest of the query. Those numbers are the reason
  `providers/github.py` is shaped the way it is.
- **`capture.py owner/repo`** — record a fresh fixture: the rows, the merge
  states, the gate of every base, the remote branches and the issue
  relations, so nothing in the suite reaches the network. Run it from the
  target repo's checkout, so `git ls-remote` sees its branches.

Two properties the suite exists to hold:

- **fetch paints facts; explicit triage publishes verdicts.** Startup and
  reload expose the provider queue without a triage grid. A triage export
  prepares the verdicts, five blocks and chase in code, then fingerprints
  every row so later provider changes become stale instead of silently
  reusing old decisions. `Blocks.test_every_row_lands_in_exactly_one_block`
  is the partition; `Gate.*` is why an approved CLEAN PR is not always yours
  to merge.
- **no prose is ever parsed back out.** `Chase` reads `waiting_on`, a field,
  never the verdict's sentence. The test that used to pin the sentence broke
  the moment the wording changed — which is the point.

Run a desk on the fixture by hand to poke at the UI:

```sh
python3 server/prdesk.py --provider fixture --repo fixture/desk --port 8396
```

Where the files go, and why the tests care: the cache, rows export and
one-shot job JSON files live under the OS temp dir — so the suite isolates
BOTH `deskstate.RUNTIME_DIR` and `deskstate.STATE_DIR`. `LaunchClearsTheCache`
uses a repo name of its own,
because the desk's background warm threads outlive the test that started them
and would otherwise write fresh entries into a shared cache.
