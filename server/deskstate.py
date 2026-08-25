"""Desk state — the file through which the skills talk to the dashboard.

The skills (pr-triage, pr-run, issue-triage) write what only a model can
produce: the diff-level analysis, the review drafts, the chase blocks, the
issue findings. The server merges it into the rows it serves, so the desk's
Analysis/Draft tabs show the skills' actual work instead of placeholders.

Path: ~/.local/state/git-workflow/<owner>__<repo>.json

Schema (all keys optional):
{
  "generated": "2026-08-25T12:00:00",
  "session":   "PR triage · genropy · 2026-08-25",
  "prs":    {"1152": {"analysis": "...", "draft": "...", "next": "..."}},
  "issues": {"1156": {"type": "DEFECT", "finding": "...", "size": "EASY",
                      "phase": "SINGLE-PHASE"}},
  "chase":  {"genro": "@genro — 7 PR ferme dal ...:\n#1027 #1044 ..."}
}
"""

import json
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "git-workflow"


def state_path(repo):
    return STATE_DIR / ("%s.json" % repo.replace("/", "__"))


def load(repo):
    path = state_path(repo)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def reset(repo):
    """Archive the previous session's state so the desk starts empty:
    stale analyses and feed lines read as fresh data otherwise. The old
    file survives as .prev next to it."""
    path = state_path(repo)
    if path.exists():
        path.replace(path.with_suffix(".json.prev"))
    inbox = STATE_DIR / ("%s__inbox.jsonl" % repo.replace("/", "__"))
    if inbox.exists():
        inbox.write_text("")


def save(repo, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(repo).write_text(json.dumps(state, indent=1))


def add_order(repo, n, propose, draft, instruction):
    """Record the user's go-ahead on one PR. /pr-run reads pending orders as
    pre-authorized work: the click in the desk was the approval."""
    state = load(repo)
    orders = state.setdefault("orders", {})
    orders[str(n)] = {"propose": propose, "draft": draft,
                      "instruction": instruction, "status": "pending"}
    save(repo, state)
    return orders[str(n)]


def annotate_prs(rows, state):
    notes = state.get("prs") or {}
    for row in rows:
        row["skill"] = notes.get(str(row["n"]))
    return rows


def annotate_issues(rows, state):
    notes = state.get("issues") or {}
    for row in rows:
        row["skill"] = notes.get(str(row["n"]))
    return rows
