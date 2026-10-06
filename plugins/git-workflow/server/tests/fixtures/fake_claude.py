#!/usr/bin/env python3
"""A stand-in for `claude -p` that answers every one-shot desk job at once
with schema-valid JSON, for end-to-end runs that spend no tokens.

Put it on PATH as `claude`. The verdict of PR n follows n % 3: approve,
changes, doubt. FAKE_FAIL=<n> makes the analysis of PR n exit 3;
FAKE_LOG=<file> appends one line per call, the job it answered."""

import json
import os
import re
import sys

prompt = sys.argv[sys.argv.index("-p") + 1]


def emit(obj):
    sys.stdout.write(json.dumps({"type": "result", "structured_output": obj}) + "\n")


def log(line):
    if os.environ.get("FAKE_LOG"):
        with open(os.environ["FAKE_LOG"], "a") as handle:
            handle.write(line + "\n")


NULL_VERDICT = {"stance": None, "why": None, "doubt": None, "lean": None,
                "hunk": None, "ask": None, "options": None}


def verdict(n):
    stance = ("approve", "changes", "doubt")[n % 3]
    out = dict(NULL_VERDICT, stance=stance, why="perché %d" % n)
    if stance == "changes":
        out["draft"] = "Please split the change for #%d." % n
    if stance == "doubt":
        out.update(doubt="dubbio su %d" % n, lean="changes",
                   hunk={"path": "gnrjs/gnrbag.js", "header": "@@ -1,3 +1,4 @@"},
                   draft="Please add a test that pins the order for #%d." % n)
    return out


if "skills/pr-analyze" in prompt:
    n = int(re.search(r"PR #(\d+)", prompt).group(1))
    log("pr %d" % n)
    if n == int(os.environ.get("FAKE_FAIL", "0")):
        sys.exit(3)
    result = {"n": n, "author": "a", "problem": "p%d" % n, "history": "h",
              "propose": "x", "draft": None, "verified": ["v"],
              "not_verified": [], "plan": None}
    result.update(verdict(n))
    emit(result)
elif "skills/issue-analyze" in prompt:
    n = int(re.search(r"issue #(\d+)", prompt).group(1))
    log("issue %d" % n)
    emit({"n": n, "type": "DEFECT", "finding": "f", "size": "EASY",
          "phase": "SINGLE-PHASE", "problem": "p", "cause": "c", "propose": "x",
          "verify": "v", "decision": None})
else:
    rows = json.load(open(re.search(r"JSON at (\S+) for", prompt).group(1)))
    if "skills/pr-triage" in prompt:
        log("pr-triage")
        prs = [dict(NULL_VERDICT, n=int(n), author=None, problem=None, history=None,
                    propose=None, draft=None, verified=[], not_verified=[], plan=None,
                    conflict_kind="mechanical", finding="f")
               for n in rows["model_tasks"]]
        emit({"flow": "pr-triage", "report": "r", "prs": prs, "issues": []})
    else:
        log("issue-triage")
        issues = [{"n": n, "type": "DEFECT", "impact": i + 1, "urgency": 2, "why": "w",
                   "after": [], "finding": "f"} for i, n in enumerate(rows["shortlist"])]
        emit({"flow": "issue-triage", "report": "r", "prs": [], "issues": issues})
