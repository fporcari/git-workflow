# Git Workflow - illustrated quick guide

This is the short version. Pick the page that matches what you need.

![Git Workflow](00-cover.webp)

## How it works

- **Reads:** pull requests, issues, reviews, branches, protection rules, and links between them.
- **Computes:** queue state, merge gates, issue cross-checks, and safe ordering in the local desk.
- **Asks a model for:** diff reading, root-cause analysis, proposals, and judgment.
- **Changes things only when:** the selected skill or an explicit approval authorizes the action.

![How it works](01-how-it-works.webp)

## Pull requests

### `pr-desk`

- **Use when:** you want the PR dashboard.
- **Starts:** a local server and opens a small web application in the browser.
- **Does:** reads and groups the PR queue; web-app buttons send selected work to the current chat.
- **Does not:** analyze every diff or act by itself.

![PR desk](02-pr-desk.webp)

### `pr-triage`

- **Use when:** you want a read-only list of every PR involving you.
- **Reads:** queue fields and the merge gate, not full diffs.
- **Does:** splits the queue into ready, small action, review, waiting, and decision blocks.
- **Next:** hands the actionable set to `pr-loop` if requested.

![PR triage](03-pr-triage.webp)

### `pr-analyze`

- **Use when:** one PR needs a proper read.
- **Reads:** the complete diff, description claims, history, reviews, and threads.
- **Returns:** what it does, what happened, what to propose, and any useful draft.
- **Does not:** post, push, merge, or modify the PR.

![PR analyze](04-pr-analyze.webp)

### `pr-loop`

- **Use when:** you want to work through PRs until nothing remains that only you can do.
- **Does directly:** only actions covered by its safe lane and current gates.
- **Asks first:** for everything requiring judgment or a user decision.
- **Accepts:** exact PR numbers and `batch=1..4`; conflicting work is sequenced.

![PR loop](05-pr-loop.webp)

## Issues

### `issue-desk`

- **Use when:** you want the issue dashboard.
- **Starts:** a local server and opens a small web application in the browser.
- **Does:** cross-checks issues, branches, and PRs; web-app buttons send selected work to the current chat.
- **Does not:** analyze every issue at startup.

![Issue desk](06-issue-desk.webp)

### `issue-triage`

- **Use when:** you want a read-only shortlist of recent, not-yet-analyzed issues.
- **Reads:** issue fields, branches, comments, and closing PR references.
- **Does:** ranks impact and classifies each item as DEFECT, REQUEST, QUESTION, or DOCS.
- **Next:** hands selected issues to `issue-loop` if requested.

![Issue triage](07-issue-triage.webp)

### `issue-analyze`

- **Use when:** one issue needs fresh analysis.
- **Checks:** root cause for defects, reuse for requests, proving code for questions, and the correct statement for docs.
- **Returns:** a typed verdict, minimum change, affected files, and verification plan.
- **Does not:** branch, comment, or modify code.

![Issue analyze](08-issue-analyze.webp)

### `issue-loop`

- **Use when:** you want issues presented one after another, or in a selected batch.
- **For each issue:** analyze fresh, show a four-line proposal, and wait for approval.
- **After approval:** claim it, fix it in a worktree, test it, and open its PR.
- **Stops:** when the selected set ends or the user says stop.

![Issue loop](09-issue-loop.webp)

## Specialist skills

### `issue-work`

- **Use when:** one issue deserves its own dedicated session.
- **Small coherent work:** analyze, fix, test, and open one PR.
- **Large work:** describe the phases and, when `phased-workflow` is installed, offer to start it from that plan.
- **Does not:** take an issue already assigned to somebody else.

![Issue work](10-issue-work.webp)

### `review-desk`

- **Use when:** you want the PR and issue dashboards together.
- **Starts:** two local desk servers and opens their small web applications in the browser.
- **Does:** routes web-app actions such as analyze, run, and triage to the current chat.
- **Keeps:** selected rows and request state synchronized between the web frontends and the chat.

![Review desk](11-review-desk.webp)

## Shared behavior

### Batch and worktrees

- A batch is limited to four proposals at a time.
- Independent work runs in separate worktrees.
- Same file, stacked PR, same issue, or unknown overlap means sequential work.
- Results are reported per item; one failure never hides the others.

![Batch and worktrees](12-batch-worktrees.webp)

### Claude and Codex

- The workflow rules, desk state, provider data, and verdicts are shared.
- Each host supplies its own adapter for questions, tasks, browser opening, and waiting.
- Host-specific mechanics must not duplicate or change workflow semantics.

![Claude and Codex](13-claude-codex.webp)
