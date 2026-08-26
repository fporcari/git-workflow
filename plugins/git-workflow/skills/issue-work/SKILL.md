---
name: issue-work
description: Work ONE issue in a dedicated session, end to end — analyze it fresh, then either fix it in a worktree and open the PR (when it is one coherent change) or lay out the phases it really needs, suggesting a phased workflow only when that plugin happens to be installed. Meant as the mandate of a session spawned for a single issue, from the review desk or by hand.
---

# Issue work — one issue, one dedicated session

Resolve `<PLUGIN_ROOT>` from `<PLUGIN_ROOT>/refs/runtime.md` before running
commands.

This session exists for one issue. Everything persisted (branch, commits,
PR, comments) in English, no AI/tool attribution anywhere; conversation in
Italian. Notify the desk at every step boundary if the state dir exists:

```bash
python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> "<one line>"
```

## 1 · Analyze first, fresh

Follow `<PLUGIN_ROOT>/skills/issue-analyze/SKILL.md` right here (this
session IS the virgin context). Outcome: type, verified root cause or gap,
minimal change, SINGLE-PHASE or WORKFLOW, EASY/MEDIUM/HARD, verification
plan, open decisions. Persist it to the desk state as that skill specifies.

## 2 · Light: worktree, fix, PR

If SINGLE-PHASE (any size): take it end to end.

- **Claim first**: `gh issue edit <n> --add-assignee @me` when unassigned;
  never touch an issue somebody else holds — report and stop instead.
- Work in the session's existing isolated worktree when the host already made
  one. Otherwise create a worktree (`git worktree add <scratch>/wt-<n>
  origin/<base>`). Branch `fix|feat|docs/<n>-<slug>`; base = where recent
  merged fix PRs target, not the harness default.
- The traps and the PR discipline are in
  `<PLUGIN_ROOT>/refs/worktree-traps.md` — verbatim rules: no
  forks/remotes, PYTHONPATH into the worktree, no git stash, narrowest check
  now, `Fixes #<n>` verified in `closingIssuesReferences`, `--assignee` the
  author, `--reviewer` from CODEOWNERS and confirmed.
- Open **draft** when a decision is open (posted on the ISSUE, linked from
  the body); ready otherwise. Never claim a verification not performed.
- Remove the worktree, report the PR link, and update the desk state entry
  (`issues.<n>.finding` gets the outcome, in Italian).

## 3 · Tough: name the phases, do not force it

If WORKFLOW (an order to respect, a suite to design, subsystems landing in
sequence): do **not** branch. Produce the phase plan — each phase one line,
with what it depends on — and:

- **If a phased-workflow plugin is installed** (check softly: a
  `wf:`-prefixed or `phased-workflow` skill appears in your available
  skills; never assume it, never require it), offer to start one from the
  phase plan and let the user decide here.
- **Otherwise** leave the plan as a comment on the issue (English), so the
  work is scoped for whoever picks it up, and say so in chat.

This skill must work identically with or without that plugin: the only
difference is whether the offer appears.

## 4 · Close the session's loop

End with: what was analyzed, what was done (PR link or phase plan), what is
left and whose move it is. One final desk notification with the same line.
