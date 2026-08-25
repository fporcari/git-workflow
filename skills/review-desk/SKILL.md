---
name: review-desk
description: Start the local review-desk dashboard — a read-only web view of the PR queue and the open issues of the current repo, with the pr-triage verdicts computed from the provider fields. Use when the user asks for the dashboard, the review desk, or a visual overview of PRs and issues. It renders state; acting on it is /pr-run and /issue-triage.
---

# Review desk

A local, read-only dashboard for the PR queue and the open issues. It shows the
same normalized rows the `pr-triage` skill reads, with the field-level verdicts
(`merge it`, `realign with the base`, `waiting on <login>`, …) computed
server-side. It never posts, merges or edits anything.

## Start it

The server lives in this plugin at `${CLAUDE_PLUGIN_ROOT}/server/prdesk.py`.
Use the browser preview (a `launch.json` entry), never a bare background shell:

```json
{
  "name": "review-desk",
  "runtimeExecutable": "python3",
  "runtimeArgs": ["${CLAUDE_PLUGIN_ROOT}/server/prdesk.py", "--port", "8399"],
  "port": 8399
}
```

With no `--repo` it reads the `origin` remote of the current directory.
Options: `--repo owner/repo`, `--provider github|forgejo`, `--me <login>`,
`--port <n>` (default 8399).

## Providers

- **github** (default) — uses the authenticated `gh` CLI; no configuration.
- **forgejo** — needs `FORGEJO_URL` and `FORGEJO_TOKEN` in the environment.

## What the columns mean

The verdict vocabulary is the pr-triage skill's section 7, restricted to what
the provider fields can honestly answer: anything needing a diff read shows as
`asks`. The `autorun` column says what `/pr-run` would do unattended (A1 merge,
A3 realign) versus what it brings to the user one PR at a time.

Data is cached for two minutes; the sync button in the top bar forces a fresh
read.

## Where the skills plug in

The desk prepares actions, it never executes them:

- every PR row carries a prepared **handoff** — the exact merge command for an
  A1, a targeted `/pr-run` prompt for anything else that is the user's move, a
  paste-ready chase line for the waiting rows — one click to copy;
- the **Chase** tab groups the people to chase; raw field grouping by default,
  replaced by the verified §6 blocks when pr-triage has exported them;
- the skills write their diff-level work to
  `~/.local/state/git-workflow/<owner>__<repo>.json` (schema in pr-triage §10):
  per-PR analysis and review drafts, per-issue findings. The desk merges the
  file into its rows live — run `/pr-triage`, `/pr-run` or `/issue-triage` and
  refresh to see their output in the detail panel.
