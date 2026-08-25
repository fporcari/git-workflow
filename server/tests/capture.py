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


def _synthetic_gates(bases, default_branch):
    """Gate shapes worth testing, invented rather than recorded: a base that
    restricts landing, the same base with an admin bypass, and a stacked PR's
    unprotected base."""
    gates = {}
    for branch in dict.fromkeys(b for b in bases if b):
        protected = branch in (default_branch, "master", "main", "develop")
        gates[branch] = {
            "branch": branch, "protected": protected,
            "approvals": 1 if protected else 0,
            "codeowners_required": protected, "codeowners_path": None,
            "owners": [], "per_path": False,
            "dismiss_stale": protected, "conversation_resolution": protected,
            "enforce_admins": protected and branch != default_branch,
            "landers": ["fixture-lander"] if protected else None,
            "permission": "write",
            "can_land": not protected,
            "bypass": protected and branch == default_branch,
        }
    return gates


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
    # NOT recorded: branch protection is readable only with admin/push access,
    # and a fixture lives in a public repo. The suite uses synthetic gates that
    # cover the same shapes — restricted base, admin bypass, unprotected base.
    gates = _synthetic_gates([r.get("base") for r in rows],
                             provider.default_branch(repo))
    relations = provider.issue_relations(repo, me)
    branches = provider.remote_branches(str(Path.cwd()))
    for row in rows:
        if row.get("merge") is None:
            row["merge"] = states.get(str(row["n"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"repo": repo, "me": me, "rows": rows,
         "queue_total": queue["total"], "queue_truncated": queue["truncated"],
         "issues": issues["rows"], "issues_total": issues["total"],
         "issues_truncated": issues["truncated"],
         "gates": gates, "issue_relations": relations, "branches": branches,
         "default_branch": provider.default_branch(repo)},
        indent=1))
    print("%s  %d/%d PR, %d/%d issue, %d gate, %d branch, %.1fs"
          % (out, len(rows), queue["total"], len(issues["rows"]), issues["total"],
             len(gates), len(branches), time.time() - t0))


if __name__ == "__main__":
    main()
