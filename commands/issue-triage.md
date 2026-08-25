---
description: Triage the 10 most recent open issues on the current repo that I have not analyzed yet — defects, feature requests, questions — rank them by impact, analyze them read-only, then present the workable ones for me to pick before any branch is cut. Explicit invocation ONLY - use exclusively when I invoke /issue-triage by name; never trigger it on your own, however well a request seems to match. Re-run to advance to the next batch.
argument-hint: optional — max PRs to open (default 5), "batch=N" to change the 10, "mine" to restrict to issues assigned to me, "report-only" to skip PR creation, "auto" to skip the selection box and open PRs for every SINGLE-PHASE issue
model: opus
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, AskUserQuestion, TaskCreate, TaskUpdate, ToolSearch, mcp__ccd_session_mgmt__set_session_title, mcp__sourcerer__kb_ask, mcp__sourcerer__kb_find_skills
---

# Issue triage — $ARGUMENTS

Conversation in Italian; everything persisted (commits, PRs, code, comments) in English.
**NO AI/tool attribution anywhere** (no Co-Authored-By, no "Generated with", no robots) — contractual obligation in genropy.

**Autonomy**: invoking this command approves the *analysis*, not the work. Steps 0-2 are read-only and run without asking. The branches, commits and PRs of Step 3 are approved by my picking the issues in the Step 2.5 box — that selection is the gate, and `auto` in `$ARGUMENTS` is the only way past it. WORKFLOW-classified issues are never in the box and are only reported. A **merge** is never autonomous.

## Step 0 — Scope

- **Date the session first, before anything else.** Read the date and the repo
  (`date +%F` and `gh repo view --json name --jq .name`), then `set_session_title`
  on `self` with `Issue triage · <repo> · <YYYY-MM-DD>`. Since a re-run advances to
  the next batch, several of these sessions pile up and an undated title makes the
  earlier batches unfindable. Read the date from `date`, never from memory or the
  context block — a mis-dated title is worse than none, because it will be trusted.
  Leave a hand-written title I chose alone.
- Repo: the `origin` remote of the current directory. My login: `gh api user --jq .login`.
- **Scope is the whole repo, not my assigned issues.** The interesting ones — a production traceback, a crash, a feature blocking someone — are routinely filed with no assignee. Restricting to `--assignee @me` silently hides them; do it only when `$ARGUMENTS` says `mine`.
- **Default batch: the 10 most recent open issues I have not analyzed yet.** "Analyzed by me" means any one of: a comment authored by my login on the issue, an open or merged PR referencing it, or **any remote branch whose name carries the issue number** — match on `/<issue-number>-`, never on a list of prefixes. Prefixes drift (`feat/` and `feature/` both exist on genropy, `hotfix/` too), and a prefix-based filter is silently wrong: it reports a branch as absent instead of erroring, so the issue looks untouched. This is what makes a re-run advance to the next batch instead of re-chewing the same ones — so **every issue in the batch must end the run carrying one of those markers** (see Step 5).
- `$ARGUMENTS`: an integer caps the number of PRs to open this run (default 5); `batch=N` changes the batch size; `mine` restricts to issues assigned to me; `report-only` means triage + report, no branches/PRs, no selection box; `auto` skips the selection box and fans out on every SINGLE-PHASE issue up to the budget — the pre-box behaviour, for when I want the run unattended.

## Step 1 — Collect, rank by impact, classify by type

```bash
ME=$(gh api user --jq .login)
gh issue list --state open --limit 300 \
    --json number,title,labels,url,author,assignees,createdAt,comments \
  | jq --arg me "$ME" '[.[] | select([.comments[].author.login] | index($me) | not)]
      | sort_by(.createdAt) | reverse | .[:10]
      | .[] | {number, createdAt, author: .author.login, labels: [.labels[].name], title}'
```

Then drop from the batch anything that already has an open PR referencing it (`gh pr list --search "<issue-number>"`, plus `Fixes #N` in open PR bodies) or a branch on origin, and pull in the next ones by date to refill to 10.

Fetch the branch list **once**, whole, and match numbers against it — do not filter by prefix:

```bash
git ls-remote --heads origin | sed 's|.*refs/heads/||' > /tmp/triage-branches.txt
for N in <the batch numbers>; do
  printf '#%s\t' "$N"; grep -E "(^|/)$N-" /tmp/triage-branches.txt | paste -sd, - ; echo
done
```

**A numbered branch always means: read its history before doing anything else.** Never hand such an issue to a fix agent before that — it would reimplement from scratch and produce a duplicate to reconcile by hand. Two checks, in this order, because they land on opposite verdicts:

1. **Is the content already on the base branch?** `git cherry` is not evidence: a squash or a rebase changes the patch-id, so it reports "not applied" for work that is fully merged. Verify a *symbol or file the branch introduces* — `git cat-file -e origin/<base>:<new file>`, `git grep <new symbol> origin/<base>`. On genropy `feature/718-afm-row-height` and `feature/874-connection-folders-cleanup` both looked unmerged to `git cherry` while their work was in `develop`, having arrived through a different PR.
2. **Is there a CLOSED PR on that branch, and why was it closed?** `gh pr list --state all --head <branch>`, then read the review comments and the closing comment. A closed PR usually carries a decision: `feature/397-…` was rejected on design ("will be done with components, not in the dispatcher"), `feature/812-…` was closed won't-fix pending a broader UI revision. Both are settled matters, not neglected work.

Only what survives both checks — content absent from the base, and no PR ever opened — is finished work nobody is reviewing, and that **is** the most valuable find of the run: on genropy `feature/1007-pdf-margin-preferences` held a complete implementation plus 333 lines of passing tests while the issue read as untouched. There, the run's output is a comment saying the work exists, what is on it, whether it still rebases onto the current base, and what it would take to open the PR. Report it on its own line in Step 5.

Two by-products of check 1 worth acting on when they show up: an **open issue whose work is already merged** should be closed with a comment naming the PR that carried it (a numbered branch pointing at a *superseded* PR is how it stays open unnoticed), and a **dead branch** — content merged, or PR closed by decision — is worth naming in the report so it can be pruned.

Fetch bodies AND comments of the batch (comments often contain "cannot reproduce", failed previous PRs, or reassignments — these change the classification).

**Rank the batch by impact, not by how small the diff is.** In descending order:

1. **evidence of real damage** — a production traceback, a crash, a hang, silent data loss or a silent no-op, anything hitting real users (the issue usually says "N occurrences in M days");
2. **blocks someone else's work** — a colleague waiting on a fix or a feature to move, a branch stuck pending a decision;
3. everything else;
4. **DOCS last, always** — a wrong doc line blocks nobody.

The ranking decides the PR budget order. An unlabelled issue whose body is a traceback outranks a `bug`-labelled cosmetic one: read the body, not the label.

Assign each one exactly one **type**, from labels first, then title markers, then the body when labels are absent or wrong (a `bug` label on a "it would be nice if…" body is a request, not a defect):

- **DEFECT** — something behaves differently from what it promises. Labels `bug`, title `[BUG]`, or a body with expected-vs-actual.
- **REQUEST** — new behaviour, option, or API. Labels `enhancement`/`feature`, title `[FEATURE]`, or a body asking for something that does not exist yet.
- **QUESTION** — how do I do X, is this supported, why does it work this way. The answer is a link to code or an explanation, not a diff.
- **DOCS** — the code is right, what is written about it is wrong or missing.

The type decides everything downstream: what the analysis agent looks for, whether a PR is even eligible, and which comment template applies.

## Step 2 — Analyze (parallel, read-only)

For each candidate spawn one read-only Explore agent (all in a single message so they run concurrently; batch in groups if more than ~6). Give the agent the type, because the question it must answer differs:

- **DEFECT** → the verified root cause, read in the actual code. Never trust the issue's own diagnosis: in past runs 2 out of 5 issue-proposed fixes were wrong or incomplete.
- **REQUEST** → walk the reuse ladder *before* designing anything: does the framework already do this (search Sourcerer KB, then the repo)? Does an existing helper cover it with a parameter? Only if nothing fits, sketch the minimal addition and name the gap it fills. A request that is already satisfied is the best outcome — but it still gets a PR (see the always-a-PR rule below), whose body opens with "this is already done today, like so".
- **QUESTION** → the answer, with the file and line that proves it, plus whether the docs should have said it (if so, note a DOCS follow-up).
- **DOCS** → the correct statement and the file that has to change.

Every agent must also return:

1. the exact minimal change respecting the repo idiom (reuse existing helpers, cite precedents);
2. **SINGLE-PHASE or WORKFLOW** — the verdict that decides autonomy (below);
3. confidence EASY / MEDIUM / HARD — this sizes the model and effort of the fix agent, not whether it runs;
4. a verification plan naming the existing test infra to use;
5. any decision left open, with the options and their consequences one line each — it goes as a comment ON THE ISSUE, addressed to the issue author with an @, BEFORE the PR is opened. The PR body links that comment; it never hosts the discussion. Reviewers review settled code, not a work in progress (cgabriel on genropy#1111, 2026-08-20).

### The always-a-PR rule

**One selected issue, one PR — every type, every confidence.** An issue filed by someone else is discussed on its diff, not on a paraphrase of it. Confidence and type never downgrade a picked issue to a comment-only outcome; only my not picking it does. What varies is only:

- **draft** when a decision is open, when the verification could not be performed, or when the issue's own premise had to be reframed;
- **ready** when the change is complete and verified.

What must never happen is a PR that picks a default **silently** — nor one that asks its design questions in its own body. An open decision is posted on the ISSUE (see point 5 above); the PR opens as **draft**, its body states which option the diff implements and links the issue comment, and reviewers are requested only once the decision is closed. A PR carrying open questions with a review request attached asks people to review a work in progress. For a request that turns out to be already satisfied: the PR body says so first, with the snippet, and the diff shows what little is actually missing.

A merge is never autonomous. A PR on a **selected** issue always is, within the limit below — no second confirmation between the box and the pushed branch.

### SINGLE-PHASE vs WORKFLOW — the autonomy boundary

- **SINGLE-PHASE**: one coherent change, one commit, one PR, verifiable in a single pass. → eligible for the Step 2.5 box; picked there, its fix agent runs in Step 3 without asking anything further. This verdict is what makes an issue *offerable*, no longer what makes it start.
- **WORKFLOW**: anything with an order to respect (phase B depends on the outcome of A), a test suite to *design* rather than write, a branch of someone else's to update and re-run CI on, or a change spanning subsystems that have to land in sequence. → **do not branch, do not fix.** Report it as needing a workflow, with the phases it would take, and leave a comment on the issue.

EASY / MEDIUM / HARD no longer gates the PR; it sizes the agent:
- **EASY** — root cause verified by reading code, local fix, or a change fully specified by the issue. Mechanical multi-site sweeps stay EASY.
- **MEDIUM** — well-specified but needs judgment beyond the issue's ask, or verification requires a live UI.
- **HARD** — the mechanism is traced but the change carries a design decision (naming a public API, choosing a default, adding a dependency, changing an existing signature), or the report was never reproduced. Still a PR, opened as **draft** with the decision in the body.

A QUESTION with no code change is the one case that ends in a comment alone — and only if there is genuinely nothing to correct in the docs.

## Step 2.5 — Present the candidates, let me pick

Nothing has been written yet: Step 2 read code, it did not touch the repo. This is where the run
asks, once, and then goes quiet again.

Skip this step entirely on `report-only` (there is nothing to select) and on `auto` (I asked for it
unattended — go straight to Step 3).

Build **one** `AskUserQuestion` with `multiSelect: true`:

- **Options: the top 4 SINGLE-PHASE issues by the Step 1 impact order.** Four is the hard cap of the
  tool, not a preference — and it sits right under the default PR budget of 5, so the box is not
  hiding work I would have gotten anyway. Everything past the fourth goes in the Step 5 report under
  *not proposed this run*, by name, so I can see what was left and ask for it.
- **WORKFLOW issues are never options.** They were never autonomous; offering them would imply a
  branch I am not going to cut.
- `label`: `#<number> — <3-5 word slug>`. Keep the number first; it is what I recognise.
- `description`: one line, in this order — **author and date** (`@login · 13 aug`), type, the
  verified root cause or the gap, and the EASY/MEDIUM/HARD size. Not the issue title paraphrased:
  what Step 2 *found*. `@genro · 13 aug · DEFECT · silent no-op in gnrbag when key is a tuple ·
  EASY` tells me whether to pick it; `bug reported by user` does not. Author and date are not
  decoration: who filed it and how long it has waited routinely decide my pick, and their absence
  forces me to ask through "Other". Append `· assigned to @<login>` when the issue already carries
  an assignee who is not me: that issue will not get an agent, and I need to see it before I spend
  a pick on it. When the issue's area plainly belongs to another maintainer's turf (their CI, their
  subsystem, a file they own), append `· likely @<login>'s` — I may want to route it rather than
  spend a pick on it.
- **Mark the recommended ones.** Append `(Recommended)` to the label of the issues with evidence of
  real damage — a traceback, a crash, data loss — and to nothing else. Impact rank 2 (blocks someone)
  is a good pick, not a recommended one; if the batch has no rank-1 issue, no option gets the mark.
  Put the recommended ones first in the list.
- `header`: `Issues`. `question`: `Quali issue lavoro in questa passata?`

What comes back is the work order for Step 3, in the impact order — not in the order I clicked. If
I pick nothing, stop after the Step 4 comments and the Step 5 report: an empty selection is a
decision, not an error, and it does not become a reason to ask again.

If I typed something in "Other" — issue numbers, a name — resolve it against the analyzed batch. A
number outside the batch has no Step 2 analysis behind it: say so in one line and leave it out
rather than handing an agent an unanalyzed issue.

An "Other" phrased as a claim rather than a selection — "questa la risolvo io", "questa me la
prendo" — is an assignment instruction first: assign the issue to me immediately, then treat it as
picked and carry on into Step 3.

## Step 3 — Fan out one fix agent per SELECTED issue (skip if report-only)

**Claim the selected issues before spawning anything.** The Softwell workflow is
assignment-based: an issue must carry an assignee *before* work on it starts, because
the assignee list is how everyone else sees who is doing what. An unassigned issue with
a PR open against it reads as still-to-do — which is the failure this step exists to
prevent, and the one somebody else then fixes by hand.

```bash
gh issue edit <n> --add-assignee @me
```

Assign every selected issue that has no assignee, and do it before the agents start,
not after the PR — the window that matters is the one while the work is being done.
Report every assignment made, in Step 5.

Which assignments are mine to make, per the workflow:

- **Backlog and historical issues: self-assignment.** Whoever can fix it takes it. This
  is the explicit exception in the policy and it needs no permission.
- **New issues awaiting the Owner's triage: ask, do not take.** New-issue assignment
  belongs to the Owner (`@genro` on genropy — the collaborator login; `gporcari` is a homonym account with no repo access). The default batch is *the ten most
  recent*, so this is the common case here, not the rare one — the Step 2.5 box is where
  I authorise it: an issue I pick, I have taken. Under `auto` there is no box, so an
  unassigned issue less than a few days old goes in the report for the Owner instead of
  being claimed silently.
- **A direct claim from me overrides both.** When I say a variant of "questa la risolvo
  io" about an issue — in the box's "Other", or in plain conversation — assign it to me
  on the spot, before any implementation, and only then go on to the branch and the PR.
  The assignment is the first action, not a by-product of opening the PR.
- **Never take an issue assigned to somebody else.** An assignee who is not me has
  already claimed it, so it gets no agent this run and its assignment is left alone —
  report it in Step 5 and move on. If I am already the assignee there is nothing to do.

Then say so on the issue, once, so the claim is visible to whoever reads the issue
rather than only to whoever reads its sidebar:

```bash
gh issue comment <n> --body "Working on this."
```

Base branch: check where recent merged fix PRs target (`gh pr list --state merged --limit 10 --json baseRefName`) — in genropy it is `develop` (hotfixes go to `master`).

Spawn **one Agent per issue I selected in Step 2.5, all in a single message so they run concurrently**, in the Step 1 impact order — not the order they came back in — up to the PR budget. A SINGLE-PHASE issue I did not select gets no agent and no branch: it goes in the report, and the next run will see it again. WORKFLOW issues never get an agent. Under `auto` the selection is every SINGLE-PHASE issue in the batch, up to the budget.

**Name the target repo correctly, once, before spawning anything.** `git remote -v` in full — do not read the first lines and guess. A clone can carry several remotes and the canonical one is not always first; cross-check with where the issues actually live (`gh issue view <n> --json url`). Every agent prompt must carry, verbatim:

> Push to `origin`. Open the PR with `gh pr create --repo <owner>/<repo> --base <base>`. Do **NOT** fork any repository, do **NOT** add git remotes, do **NOT** open a PR against any other repo. If a push is rejected for permissions, STOP and report — do not route around it.

That paragraph is not boilerplate. Given a repo it cannot push to, an agent will fork to obtain write access and open the PR on the wrong upstream — creating a public repo nobody asked for. Worktrees share the clone's git config, so one agent adding a remote pollutes every sibling agent's environment.

**Warn every agent about the worktree-invisibility traps.** A green run from a worktree can prove nothing at all, in two independent ways:

- **`gnr.*` imports** — in an editable install they resolve to the MAIN checkout, not the worktree. Each agent must assert `module.__file__` points inside its own worktree before believing any result, and prepend `PYTHONPATH=<worktree>/gnrpy`.
- **anything under `resources/` or `projects/`** — `~/.gnr/environment.xml` hardcodes those to the main checkout, so a resource added in a worktree is invisible to the resource loader no matter what `PYTHONPATH` says. It needs a scratch `GENRO_GNRFOLDER` pointing `resources` and `projects` at the worktree, and **the directory must be named `gnr`** (the config Bag keys off the dirname). Confirm `resources_dirs` actually contains the worktree path before trusting the test.

The pre-commit hook is unaffected — it runs against the worktree correctly.

Per agent:

- `isolation: "worktree"` — **mandatory.** Concurrent `git checkout -b` in one clone collide.
- `model` / `effort` by confidence: EASY one-liner → `sonnet`, low effort; EASY multi-site sweep → `sonnet`, high effort; MEDIUM or HARD → `opus`, high effort.
- Hand it **the analyst's verified conclusions verbatim** — root cause, `file:line`, the minimal diff, the precedents cited, the verification plan, and any open decision to put in the body. Without them the agent re-derives the analysis and drifts off it.
- Tell it explicitly whether its PR opens as **draft** or **ready**.

Each agent owns its whole cycle:

1. `git checkout -b fix/<issue>-<slug> origin/<base>` (`feat/<issue>-<slug>` for a REQUEST, `docs/<issue>-<slug>` for DOCS)
2. Apply the minimal change. Real tests, not cosmetic mocks: use the repo's test infra (`test_invoice` app, `db_pg` fixture, daemon tests). JS-only changes: `node --check` + document the manual browser steps honestly.

   For a live browser check, **name an existing instance in the prompt** — an agent told only "serve the app" will either burn its budget hunting for one or stop. Find one first (`projects/*/instances/`, plus the roots listed in `~/.gnr/environment.xml`) and hand it over with its port. The safe recipe: `gnrConfigPath()` honours `GENRO_GNRFOLDER` (`gnrpy/gnr/core/gnrconfig.py`), so copy `~/.gnr` into the scratchpad, repoint only the static path at the worktree, serve on a spare port, and confirm by curling the served asset. **Never let an agent create a site or a database to get a live check**, and never let it write to the real `~/.gnr`; "not verified live" plus exact manual steps is the correct outcome when no instance fits.
3. Narrowest check NOW: flake8 on touched py files + the targeted tests, then the relevant suite.
4. Pre-commit hook runs the full suite. If a failure is **pre-existing**, prove it and document it, then `--no-verify`; otherwise fix or drop the branch. Note: N agents means N full suites on one machine — expect it to be slow, do not let a timeout be mistaken for a failure.

   **Full-suite counts from concurrent agents are worthless, and the cause is not a race — it is deliberate.** `get_pg_config()` (`gnrpy/tests/sql/common.py:67`) runs `subprocess.run(['pkill','-f','postgres.*tmp'])` before starting its own ephemeral Postgres: every agent that begins a `tests/sql/` run **kills every other agent's database mid-test**. Worse, `db_pg`/`db_pg3` (`gnrpy/tests/sql/conftest.py:122,142`) call `get_pg_config()` *outside* their `try`, so a hung `initdb` left by a murdered run makes the fixture **error instead of skip**. Symptoms seen in one night: `'the database system is shutting down'`, `RuntimeError: initdb failed`, `Connection refused`, and failure counts swinging by hundreds across identical runs.

   Consequence for the fan-out: only failures **outside** `tests/sql/` are evidence. Tell each agent to run `pytest gnrpy/tests/ -q --ignore=gnrpy/tests/sql` for its gate, and to treat anything under `sql/` as environmental unless it reproduces on a quiet machine. Also: `gnrbag_test.py::test_fillFromUrl` is not a reliable known-failure — it fetches a remote URL and passes whenever the network is up, so do not hand it to agents as a fixed expectation.

   **Never use `git stash` to prove a failure pre-existing.** Worktrees share one `.git`, so `refs/stash` is a single global stack — `git stash pop` takes whatever is on top, which in a real repo is very likely someone's months-old entry or a sibling agent's work, not your own. Set the change aside with a patch file instead:

   ```bash
   git diff > /tmp/<branch>.patch && git checkout -- .
   # run the single failing test on the clean tree
   git apply /tmp/<branch>.patch
   ```

   If a stash push already happened, recover by its immutable hash from `git stash list --format='%H %gd %s'`, never by index — the index shifts under concurrent agents.
5. Commit message: `fix(<area>): <summary>` / `feat(<area>): <summary>` / `docs(<area>): <summary>`, body explains root cause or motivation and what was verified. `Fixes #<n>`. No attribution lines.
6. Push, then `gh pr create` (`--draft` when told) with body sections: Problem / Root cause (DEFECT: including corrections to the issue's premise) or Motivation (REQUEST) / Change / Verification / Open decision (when there is one, addressed to the issue author with an @). Never claim a verification that was not performed — write "not verified live" with the exact manual steps instead.

   **The body carries the closing reference, and the issue carries an assignee.** `Fixes #<n>` in the commit message is not enough on its own: put it in the PR body too, under a *Related issue* line, and then verify GitHub actually linked it — an unlinked PR leaves the issue looking untouched in every issue list, which is the whole defect this guards against.

   ```bash
   gh pr view <n> --repo <owner/repo> --json closingIssuesReferences
   gh issue view <issue> --repo <owner/repo> --json assignees
   ```

   An empty `closingIssuesReferences` means the keyword did not take — fix the body. An empty `assignees` at this point means Step 3's claim was skipped: **assign the issue to the PR author before moving on**, and say in Step 5 that it was caught here rather than at claim time.

   ```bash
   gh issue edit <issue> --repo <owner/repo> --add-assignee <pr-author>
   ```

   **Every PR opens with a reviewer on it — pass `--reviewer` explicitly, never rely on CODEOWNERS to do it.** GitHub auto-assigns a codeowner only for paths some rule actually matches: a PR confined to a directory with no rule and no `*` catch-all opens with zero reviewers, nobody is notified, and it sits in `REVIEW_REQUIRED` indefinitely looking green. Resolve the reviewer before opening — read `.github/CODEOWNERS` on the base branch, match it against the paths in the diff, and fall back to the repo's usual codeowners when no rule covers them:

   **And an assignee, which is a different field and a different role.** `--reviewer` asks
   somebody for a read; `--assignee` says who answers for the PR and who will merge it. The
   assignee of a PR is its author, so every PR an agent opens carries `--assignee` set to
   the author — a PR with nobody on the hook for closing it is how a green, approved branch
   sits unmerged for a week.

   ```bash
   gh api "repos/<owner/repo>/contents/.github/CODEOWNERS?ref=<base>" --jq '.content' | base64 -d
   gh pr create --repo <owner/repo> --base <base> --assignee <author> --reviewer <login>[,<login>] ...
   ```

   Then confirm it landed — `--reviewer` fails silently for a login without repo access, and a self-review request is dropped without an error:

   ```bash
   gh pr view <n> --repo <owner/repo> --json reviewRequests
   ```

   An empty `reviewRequests` after that is a finding, not a detail: report it in Step 5 with the paths that no CODEOWNERS rule covers, so the file gets fixed once instead of every PR being patched by hand.
7. Return the PR url, whether it is draft, **the PR's assignee and who it was assigned to for review**, and what was verified vs left manual.

Along the way: flag out-of-scope discoveries (pre-existing failing tests, duplicated buggy code) as separate tasks instead of widening the diff.

## Step 4 — Unblock the rest (comments on GitHub)

One comment per issue (English, no AI references, never on issues where the ball is already in MY court — e.g. someone asked ME for details). Skip issues already carrying an equivalent unanswered request. **"The rest" excludes the issues I analyzed and did not pick**: nothing is blocked on their author, the analysis is done, and a comment there would only publish my scheduling to the repo. They stay silent and unmarked, so the next run offers them again. Before commenting, fetch each issue's author (`--json author`) so clarifications are addressed to the right person, and mention them with @.

- **DEFECT, unclear** (vague description, no expected-vs-actual): ask for the specific missing pieces — steps, expected vs actual, error text, minimal example.
- **DEFECT, hard to reproduce**: FIRST check whether a test case already exists (test package webpages `projects/gnrcore/packages/test/webpages/`, `gnrcomponents/testhandler` `test_*` pages, `gnrpy/tests`, `test_invoice` models) and cite what you find: if one exists, ask whether the bug reproduces there; if none, request a minimal committed test case. If existing tests PASS on the reported feature, say so — it narrows the bug to the reporter's specific pattern.
- **REQUEST already satisfied**: say how it is done today, with the file, the API and a short snippet, and ask whether that covers the case — linking the draft PR that shows it.
- **REQUEST with a design decision open**: the decision lives in the draft PR body; the comment just points at it. Do not decide it, here or there.
- **QUESTION**: answer it, citing the code that proves the answer. If the docs should have carried that answer, the docs fix is its own PR.
- **WORKFLOW-classified**: say what the fix needs — the phases, in order, and what each one depends on — so the issue records why it was not resolved in this pass.

**Label what you classified.** A triage that leaves no trace on the issue is a triage
nobody else can see. When an issue carries no type label and the repo defines one that
fits, add it — read the repo's own set first (`gh label list`), never invent a name and
never assume the `priority:*`/`size:*`/`area:*` families exist, since most repos do not
define them (genropy carries `bug`, `enhancement`, `question`, `documentation`, `task`,
`refactor`, `hotfix`, plus `Review effort N/5` and `Low effort`):

```bash
gh issue edit <n> --add-label <label>
```

Do not relabel what somebody has already labelled: a wrong existing label is a line in
the report, not a silent correction.

## Step 5 — Report, then summary

**Report** — schematic, one table per block:
- PRs opened: issue → type → PR link → draft/ready → one-line change → what was verified → **assignee of the issue and reviewer of the PR** (the workflow requires both; an empty one is a finding, not a detail);
- WORKFLOW-classified: issue → why one phase is not enough → the phases it would take, in order;
- left to me for a decision: issue → the choice → where it is written (draft PR link);
- **finished work waiting, no PR**: issue → the branch → what is on it → whether it still rebases onto the current base → what it needs to become a PR;
- **analyzed, not proposed**: the SINGLE-PHASE issues that fell outside the box's four slots → type → the one-line finding Step 2 produced → EASY/MEDIUM/HARD. This block is the box's overflow: the analysis exists and is not thrown away, so I can ask for any of them straight away without a re-run;
- **proposed, not picked**: the ones I saw in the box and left out → the same one-line finding. Say nothing about why — that was my call;
- skipped: already has PR/branch, or budget cap reached.

**Summary** — a short prose close after the tables, three or four lines: what this batch actually was (the shape of the defects found), the one thing worth acting on first, and what the next run will pick up.

Then check the batch is closed. Every one of the 10 must carry a PR or a comment, otherwise the next run picks it up again — and with the selection box that is now true **by design** for one class: an issue I analyzed but did not pick gets no PR, and the Step 4 comment rules do not cover it either (there is nothing to ask its author — the analysis is done, the work simply was not chosen). Leave those unmarked deliberately, list them in the two blocks above, and say in the summary that the next run will legitimately see them again. That is the mechanism by which an unpicked issue comes back, not a leak. Any *other* issue examined and left unmarked still has to be named with its reason.

## Step 6 — Knowledge

If a non-obvious GenroPy lesson emerged (framework mechanism, test-infra trick, an API that already covered a request), propose `kb_add_skill` to Sourcerer.
