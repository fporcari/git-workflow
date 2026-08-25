# The desk's test environment

No network, no GitHub, no rate limit — a recorded payload replayed by the
`fixture` provider.

```sh
server/tests/run.sh              # server tests + UI tests
server/tests/run.sh --bench      # plus the live benchmark (needs gh)
```

- **`test_desk.py`** (28 tests, stdlib unittest) — the row contract the
  skills read, the verdict engine, the cache's stale-while-revalidate and
  single-flight behaviour, every HTTP endpoint including the 304 path, and
  the guarantee that a cold snapshot pays its three cache misses in
  parallel rather than one after the other.
- **`test_ui.mjs`** (25 checks, plain node) — drives the **real**
  `static/index.html` against a **real** desk process on the fixture
  provider, through a small DOM shim. It is the page's own render path that
  runs, so it catches a render that throws, a missing field, a button wired
  to nothing, a detail tab that comes up blank.
- **`bench.py`** — where the time goes. `--queries` is the interesting one:
  it shows that the search itself is cheap, that resolving the per-PR
  nested connections is what costs, and that `mergeStateStatus` alone costs
  more than the whole rest of the query. Those numbers are the reason
  `providers/github.py` is shaped the way it is.
- **`capture.py owner/repo`** — record a fresh fixture.

Run a desk on the fixture by hand to poke at the UI:

```sh
python3 server/prdesk.py --provider fixture --repo fixture/desk --port 8396
```
