"""Record a live provider payload as a test fixture.

    python3 tests/capture.py owner/repo [login] [outfile]

The fixture provider replays it, so the UI, the HTTP layer and the benchmark
all run at full speed with no network and no rate limit.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers import get_provider  # noqa: E402


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    repo = sys.argv[1]
    provider = get_provider("github")
    me = sys.argv[2] if len(sys.argv) > 2 else provider.whoami()
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else \
        Path(__file__).resolve().parent / "fixtures" / (repo.split("/")[-1] + ".json")

    t0 = time.time()
    queue = provider.queue(repo, me)
    states = provider.mergestates(repo, me)
    issues = provider.issues(repo)
    rows = queue["rows"]
    for row in rows:
        if row.get("merge") is None:
            row["merge"] = states.get(str(row["n"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"repo": repo, "me": me, "rows": rows,
                               "issues": issues}, indent=1))
    print("%s  %d PR, %d issue, %.1fs" % (out, len(rows), len(issues), time.time() - t0))


if __name__ == "__main__":
    main()
