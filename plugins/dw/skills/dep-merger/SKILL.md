---
name: dep-merger
description: Release checklist for a repo where every change lands on an integration branch (develop) and production (master) takes only what a reviewer approves. Finds, PR by PR with author and issue, what develop carries that master lacks — recognising what already went over by cherry-pick or by a twin PR — proves the list complete with a trial cherry-pick, renders one standalone HTML page where the reviewer ticks what goes to production and exports the cherry-pick list, then applies a pasted list on a local release branch. Invoked explicitly only.
effort: high
disable-model-invocation: true
---

# dep-merger

Two modes. **Build**: from the branches to a page the reviewer ticks. **Apply**: from
the pasted cherry-pick list to a release branch. Scripts are stdlib Python 3.9+; resolve
this skill's directory from the loaded SKILL.md location.

## Build

1. **Branches.** Production is the repo's GitHub default branch
   (`gh repo view --json defaultBranchRef`); the integration branch is the one PRs
   target, from the project CLAUDE.md (on Softwell repos, `develop`). Ask only if
   neither says.
2. **Analyse, read-only.**
   ```sh
   python3 <skill-dir>/scripts/analyze.py --base master --head develop \
       --scratch <scratchpad> --out <scratchpad>/analysis.json
   ```
   It prints one line per first-parent commit of develop not reachable from master:
   - `in_base cherry-picked`: master has a commit with its `cherry picked from` trailer.
   - `in_base same content`: the change reverse-applies on master (a twin PR, a hotfix).
   - `missing clean` / `missing 3way`: not on master, applies on it alone.
   - `unclear conflict`: does not apply alone. Usually it depends on an earlier unit;
     `maybe_requires` in the JSON lists the earlier units touching the same files.
   Then it cherry-picks every missing and unclear unit, in develop order, in a
   throwaway worktree: `trial ok` per unit, and `residual diff: none` means the list is
   complete. A residual diff or a `trial conflict` is a finding to explain before
   building the page, never something to hide.
3. **Group into reviewable units.** One unit per PR. A run of direct commits that
   follows up a PR (same author, same feature, same files) joins that PR's unit as
   extra picks; a direct commit that stands alone stays its own unit, labelled by its
   short sha. A commit that is in a unit's run but makes sense alone (a small fix)
   can also be its own unit: the export dedupes shared picks.
   - `requires`: a unit whose picks conflict without another unit. Prove it: replay
     the unit alone on master in a scratch worktree, then with the candidate first.
   - `options`: picks inside a unit that the reviewer may leave out (an instance-wide
     flag riding along a feature). Mark them `opt` and default them off unless the
     change is part of the feature.
   - `db`: a unit adding columns, tables or upgrades (`schema` in the analysis);
     `post_deploy` names the command (`gnr db migrate "<instance>.*" -u` on GenroPy).
   - Titles in the page language; `note` for what the reviewer needs to decide
     (a twin PR closed unmerged, a risky side change).
4. **Write the checklist JSON** (schema below) and render it:
   ```sh
   python3 <skill-dir>/scripts/render.py checklist.json -o <repo>-release-<date>.html
   ```
   Both in the scratchpad or the folder the user names, never in the repository.
5. **Prove the export before delivering.** Open the page in the browser pane over a
   local static server, tick every unit, take the list, and replay its picks on
   master in a scratch worktree: every pick must pass and the tree must equal develop.
   Then remove the worktree. Without this run, say "export not verified".
6. **Deliver** the HTML as a file (SendUserFile, display `attach`) with one line: tick,
   add notes, press «Copia lista cherry-pick», paste it in chat. Report the units in
   chat too, grouped by author, so the user can answer without opening the page.

## Apply

The pasted list is the approval for a LOCAL release branch only.

1. Check the header: repo, base, date. A list older than the base tip → rerun the
   analysis; master may have moved.
2. Read every `# nota`, `# ATTENZIONE` and option line first; a note that changes the
   plan is a question for the user before any pick.
3. Run the `git fetch` / `git switch -c` lines, then the picks one at a time. A
   conflict → stop, `git cherry-pick --abort`, report the pick and the files; never
   resolve it unasked.
4. Run the project's narrowest checks on the touched packages (lint, the tests the
   picks brought).
5. Push, the PR to master and its assignee follow the repo's rules (project CLAUDE.md,
   the global Git rules): ask before pushing. The PR body lists the included units and
   the left-out ones, as release PRs before it did, plus the `post_deploy` command when
   a `db` unit is in.

## Checklist JSON

```json
{
  "repo": "owner/name",
  "base": "master",
  "head": "develop",
  "remote": "origin",
  "date": "YYYY-MM-DD",
  "branch": "release/YYYYMMDD",
  "title": "Name of the page",
  "meta": "date · what it is",
  "lead": "What the reviewer does, one or two sentences.",
  "post_deploy": "command printed when a db unit is ticked",
  "order": ["every first-parent sha of base..head, oldest first (analysis.order)"],
  "units": [{
    "id": "183",
    "label": "#183",
    "author": "github-login",
    "issues": [162],
    "title": "what changes, as the reviewer reads it",
    "note": "optional",
    "db": "optional: what touches the schema",
    "requires": ["181"],
    "options": [{"key": "expflags", "label": "Include `94db2182` ...", "default": false}],
    "picks": [{"sha": "<40 hex>", "m": true, "opt": "expflags", "what": "optional comment"}]
  }],
  "already": ["already on base, one line per group"],
  "open": ["open PRs on head, not included"],
  "footer": "optional"
}
```

`m: true` is a merge commit (`cherry-pick -m 1`). Text fields take `code`. The export
sorts all ticked picks by `order`, so dependent units come out in develop order however
they were ticked. A ticked unit whose `requires` is not ticked shows an alert on the
page and an `# ATTENZIONE` line in the list.
