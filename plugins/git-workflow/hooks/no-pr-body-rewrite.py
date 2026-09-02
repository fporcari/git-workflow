#!/usr/bin/env python3
"""Block any rewrite of a pull request's description from a Bash call.

The body is the author's record of the change as it was opened. A review is
answered in a comment or in its thread, never by editing that record: once a
body is rewritten to fit a comment, the description stops matching what was
reviewed and nobody can reconstruct what changed hands (genropy#1054). The
user edits a body by hand, from a terminal, when the approach really moved.

Claude Code only: Codex has no hooks, runtime.md carries the rule there.
Fails OPEN on an unparseable command: this guards one gesture, not the shell.
"""
import json
import re
import shlex
import sys

SEPARATORS = {"&&", "||", ";", "|", "&"}
BODY_FLAGS = {"-b", "--body", "-F", "--body-file"}
FIELD_FLAGS = {"-f", "-F", "--field", "--raw-field"}
PULL_PATH = re.compile(r"(^|/)pulls/\d+/?$")


def _segments(command):
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        return []
    out, cur = [], []
    for tok in tokens:
        if tok in SEPARATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _flag(tok):
    return tok.split("=", 1)[0]


def rewrites_body(segment):
    while segment and "=" in segment[0] and not segment[0].startswith("-"):
        segment = segment[1:]                       # leading VAR=value
    if len(segment) < 3 or segment[0] != "gh":
        return False
    if segment[1] == "pr" and segment[2] == "edit":
        return any(_flag(tok) in BODY_FLAGS for tok in segment[3:])
    if segment[1] == "api":
        rest = segment[2:]
        if "graphql" in rest:
            joined = " ".join(rest)
            return "updatePullRequest" in joined and re.search(r"\bbody\s*:", joined) is not None
        on_pull = any(PULL_PATH.search(tok) for tok in rest if not tok.startswith("-"))
        if not on_pull:
            return False
        method = None
        sets_body = False
        for i, tok in enumerate(rest):
            flag = _flag(tok)
            value = tok.split("=", 1)[1] if "=" in tok else (rest[i + 1] if i + 1 < len(rest) else "")
            if flag in ("-X", "--method"):
                method = value.upper()
            elif flag in FIELD_FLAGS and value.split("=", 1)[0] == "body":
                sets_body = True
            elif flag == "--input":
                sets_body = True
        return sets_body or method == "PATCH"
    return False


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not any(rewrites_body(seg) for seg in _segments(command)):
        return 0
    print(
        "This command rewrites a pull request's description. The body is the "
        "author's record of the change as opened: answer a review in a comment "
        "(`gh pr comment`) or in its thread, never by editing the body. If the "
        "approach really moved and the description is now wrong, say so in the "
        "comment and leave the body edit to the user, by hand.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
