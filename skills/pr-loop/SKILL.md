---
name: pr-loop
description: Loop over the PR queue until the user has nothing left that only he can do — first the moves that need no permission (merge his own fully-approved PRs, answer small named review requests, realign his DIRTY branches), then the rest presented for a go-ahead, one at a time or in a batch. Takes the numbers to work and a batch size, so several can be proposed together and executed in parallel worktrees.
argument-hint: opzionale — "1145,1128,1059" per lavorare solo quelle · "batch=4" per proporne 4 insieme
disable-model-invocation: true
---

# PR loop

## The goal

**The user has nothing left on his plate that only he can do.**

Not "every PR merged" — most of the queue is waiting on other people and will
still be waiting when this skill finishes. The target is the subset that is his
move, driven to zero: merged, answered, realigned, or handed back to him as a
decision he has actually made.

**Done when** every PR he is involved in is in one of three states:

1. settled — merged, or answered and the ball is demonstrably somebody else's;
2. waiting on a named person who is not him;
3. waiting on him, and he has been told so in a line he has read.

State 3 counts as done. A PR needing him to finish unfinished work, mark a draft
ready, or chase a reviewer is not a failure of this skill — leaving him to
discover it later is.

**Not done** while any PR is his move and he has not been told: an approved PR
nobody merged, a `DIRTY` branch, a review request sitting on him, a
`CHANGES_REQUESTED` he never answered.

## The mandate: `$ARGUMENTS`

| what he typed | what it means |
|---|---|
| `1145,1128,1059` (`#` optional) | **the working set**: exactly those, **in that order**, and then stop. No other PR is read, merged, answered or realigned — including by Lane A |
| `batch=N` | present N together in Lane B instead of one. Clamped to **1..4** |
| anything else | a scope note, not a filter — say back how you read it |

**Default `batch=1`.** One at a time is the shape that costs nothing when he
says no; `batch` is him saying *"I have already decided, spend the reads"*.
Clamped to 4 because one `AskUserQuestion` box holds four options, and a
fifth proposal would cost the answer its clickable path for nothing.

**A named working set narrows Lane A too.** `/pr-loop 1145` means that PR
and no other: an approved PR of his elsewhere in the queue is not touched,
and the closing report says so rather than pretending the queue was drained.

**From a desk button** the event is
`{"kind": "run", "flow": "pr-loop", "ns": [...], "batch": N}`. `ns` is the
rows he picked by hand in the dashboard, and it means what the typed list
means. Do not re-ask which ones — the picking was the answer.

## The loop

Two lanes, in order, and Lane A **iterates to a fixed point** before Lane B
starts:

```
repeat:
    read the queue fresh
    do every A1, A2, A3 that qualifies
until a full pass changes nothing
then Lane B, one PR at a time (or one batch at a time)
report against the goal
```

The iteration is not ceremony. Lane A's own actions change the queue: merging a
PR can bring a stacked one from unmergeable to mergeable, realigning a branch can
turn it into an A1, and a merge into the base can put a second PR into conflict.
One pass leaves work on the table that the same pass created.

Re-read the fields on each pass rather than trusting the previous one. Stop
iterating when a pass finds nothing — never on a count of passes.

**Lane A is the work the user delegated by invoking this skill**: do it, report
it, do not ask. **Lane B is everything else**, presented one PR at a time.
Invoking this skill IS the authorisation for Lane A — that is the whole point of
it being a separate, explicitly-invoked skill. It is not authorisation for
anything outside the three operations below.

**A veto on the grid outranks the lane.** `pr-triage` shows an `autorun` column
precisely so the user can say "not that one" before anything runs. If he did,
that PR is Lane B no matter how cleanly it passes A1's gates — and if the grid is
older than the queue, re-read the fields rather than trusting its cells: an
approval can have landed, or fallen, since it was printed.

**Entered from `pr-triage`, the veto is already collected** — that skill asks its
one question before handing over, and its answer is the exclusion list. Do not
ask again: a second confirmation turns a mandatory handover back into the manual
driving the split was meant to end.

**Invoked with no arguments and no grid, there is no veto.** Take the queue as it stands
and say, in one line before acting, which PRs Lane A is about to touch. Same
information as the `autorun` column, arriving a moment later. Never refuse to
start for want of a triage: this skill reads the queue itself on every pass by
design, and a desk hands it the rows already read.

**When in doubt about a lane, the answer is Lane B.** Getting a PR wrong
unattended costs more than one question.

## Reading the state

`pr-triage` carries the full field semantics. The gates this skill needs before
it may act:

- **`isDraft` overrides everything.** A draft is not waiting on reviewers and
  cannot be merged. Lane A never touches a draft except to realign it.
- **An approval counts only on the current head** — read `commit_id`, not
  `state`; a `DISMISSED` entry counts for nothing.
- **CODEOWNERS decides whose approval is required**; a standing request from a
  non-codeowner is a courtesy and does not block protection.
- **`CHANGES_REQUESTED` is sticky.** Read the timeline for whose turn it is; if
  the user's own comment is the last event, he has already answered and there is
  nothing to do.
- **`CLEAN` means nothing on an unprotected base** — read `baseRefName`.

```bash
gh pr view <n> --json isDraft,baseRefName,mergeStateStatus,reviewDecision,reviewRequests,reviews,assignees,author,headRefOid \
  --jq '{draft:.isDraft,base:.baseRefName,merge:.mergeStateStatus,dec:.reviewDecision,
         pend:[.reviewRequests[].login],revs:[.reviews[]|"\(.author.login):\(.state)"],
         assg:[.assignees[].login],author:.author.login,sha:.headRefOid[0:9]}'
gh api repos/<owner/repo>/pulls/<n>/reviews --jq '.[]|"\(.user.login) \(.state) commit=\(.commit_id[0:9])"'
```

# Lane 0 — desk orders

Before Lane A, read `orders` in
`~/.local/state/git-workflow/<owner>__<repo>.json`. Each `"status": "pending"`
entry is a decision the user already took in the review-desk dashboard: the
pr-analyze block was shown to him and he clicked go — with an optional
instruction typed in. That click is the authorization, like invoking this
skill is for Lane A: execute the order first, without re-asking.

- An empty or `vai`/`ok` instruction means execute `propose` as it stands;
  any other text refines or overrides it — the user's text wins.
- The operation follows the same rules as its Lane A/B counterpart (A2
  discipline for answers, A1 gates re-checked fresh before any merge, A3
  rules for realigns). An order never widens beyond what it names.
- When done, set the order's `status` to `"done"` plus a one-line `report`;
  on failure `"failed"` with why. An order needing a decision its instruction
  does not cover goes to Lane B and is marked `"needs-input"`.

# Lane A — no permission needed

Three operations, on the user's own PRs only. Everything else is Lane B.

## A1. Merge his own fully-approved PRs

All of these, checked fresh, or it is not an A1:

1. `author.login == @me`, and the assignee is the author — the merge belongs to
   the assignee, which is why this never extends to anybody else's PR;
2. `isDraft == false`;
3. `reviewDecision == "APPROVED"`, zero standing `reviewRequests`, no
   `CHANGES_REQUESTED`, no comment-only answer left unanswered;
4. every approval on the **current head**;
5. `mergeStateStatus == "CLEAN"` on a **protected** base.

Squash when the branch carries fixups or merge commits, delete the branch, then
verify the linked issues actually closed.

```bash
gh pr merge <n> --squash --delete-branch
gh pr view <n> --json state,mergedAt,closingIssuesReferences
gh issue view <issue> --json state
```

**Never merge a PR the user did not author**, whatever its state, and never merge
one failing any of the five — report it as a Lane B row instead. An order to
drain the queue selects which mergeable PRs to merge; it does not make an
unmergeable one mergeable.

If a merged PR closed no issue because none was linked, say so — the traceability
gap survives the merge.

## A2. Answer a small, named review request

Autonomous only when the reviewer did the thinking for you. All four:

- the ask is **named**, not inferred — the reviewer wrote the replacement, or the
  change is one obvious edit in one place;
- it is **local**: one function or one file, no new abstraction, no new
  dependency, no test rewritten;
- it does **not change the design or the scope** of the PR;
- its correctness is **checkable now**, by the narrowest test plus the linter.

The model is a reviewer saying "these three lines can be one, here it is". The
counter-model is a reviewer reporting a symptom that needs analysis before anyone
knows what the fix is — that is Lane B, however short the eventual patch.

**Verify the premises before accepting.** A reviewer's reasoning is a claim like
any other: go read the function his argument rests on. Accepting a wrong
simplification autonomously is the one way this operation does damage. If a
premise does not hold, do not implement and do not argue in a vacuum — move the
PR to Lane B with what you found.

Then: implement, run the narrowest check plus the linter, push, and answer in the
thread. Quote the reviewer's own sentence as a blockquote with a permalink to the
review — a review body has no thread of its own, so without the quote nobody can
tell what the answer answers. Re-request the review once, on the push that
answers it.

```bash
gh api repos/<owner/repo>/pulls/<n>/reviews --jq '.[]|{id,user:.user.login,state}'   # for the permalink id
gh pr comment <n> --body-file <f>
gh pr edit <n> --add-reviewer <login>
```

**Weigh the dismissal cost first and say it in the comment.** With
`dismiss_stale_reviews` on the base, the push drops every approval the PR had. On
a PR that is otherwise ready that is a real cost — but a PR that owes a change
cannot merge either, so make the change, then say plainly in the comment that the
approvals fell and nothing they approved has moved. Nobody should have to work
out why their approval vanished.

Answer inline comments **inside their own thread**, so the answer sits next to
the line and the reviewer can resolve it:

```bash
gh api repos/<owner/repo>/pulls/<n>/comments --jq '.[]|{id,path,line,user:.user.login,body}'
gh api repos/<owner/repo>/pulls/<n>/comments/<comment_id>/replies -f body="..."
```

**One comment per round.** If you already replied and then pushed more, extend
it with `gh pr comment <n> --edit-last` rather than stacking a third telling.

## A3. Realign his DIRTY branches

**Merge the base in. Never rebase, never force-push.** "Update the PR", a
`DIRTY`/`CONFLICTING` state, or any wording about realigning means GitHub's
"Update branch": `git merge origin/<base>`, in a throwaway worktree so the user's
tree stays put.

Read the conflict before deciding it is an A3:

```bash
git fetch origin +refs/pull/<n>/head:refs/remotes/pr/<n>
git merge-tree --write-tree --name-only pr/<n> origin/<base> | tail -6
```

- **A shared changelog, version or lock file** — mechanical, keep both sides in
  the file's own order. This is the common case and it is pure A3.
- **Two sides appending to the same place in a test file** — check the helper and
  test names on both sides; disjoint names mean keep both, and then run that
  whole file, including the tests that arrived from the base. That is the check
  that the resolution did not quietly break what just landed.
- **A conflict in a file the base itself rewrote** — **not** A3. Resolving it is
  design work, and if the base is still under review its shape will move and the
  resolution will be thrown away. Document it on the PR instead: what the
  textual conflict is, which incompatibilities the merge does *not* fix, and what
  it costs — then leave it `DIRTY` with the reason written down where the next
  person will find it. A stacked PR is the usual case.

**Resolve conflicts line by line, never by string index.** `s.index('=======')`
finds the marker inside an RST or Markdown underline — `==========` contains
`=======\n` at an offset — and silently eats a heading. Walk the lines with a
three-state machine on `<<<<<<< `, `=======`, `>>>>>>> ` and assert you ended in
the plain state.

Let the repo's own commit hook run: it is the linter and the suite, and its real
numbers are what you report. Only amend the merge commit's **message** with
`--no-verify`, and only while it is still unpushed. Then a plain push.

```bash
git worktree add <scratch>/wt --detach pr/<n>
cd <scratch>/wt && git merge origin/<base> --no-edit
# resolve, git add, git commit --no-edit          <- hook runs flake8 + suite here
git commit --amend --no-verify -m "Merge branch '<base>' into <head-branch>"
git push origin HEAD:<head-branch>
git worktree remove <scratch>/wt --force
```

Verify the state actually changed — `DIRTY` should become `BLOCKED` or `CLEAN` —
and if the PR was approved, re-request and comment as in A2.

## Not Lane A, ever

- Any PR the user did not author.
- Reviewing, approving or requesting changes on anybody's PR.
- Merging on a non-protected base, or with a check failing.
- Force-pushing, or rewriting pushed history. A permission block on a
  force-push is the rule working, not an obstacle: stop, say why, hand the user
  the command.
- Opening issues, marking a draft ready, closing a PR, changing assignees on
  somebody else's PR.
- Anything a reviewer asked for that needs a decision about design or scope.

## Between the lanes

Report Lane A as a table of what was done and the real check numbers, then say
what is left. If Lane A did nothing, say that too — it means the queue's
remaining work all needs him.

# Lane B — one PR at a time, or one batch

Ordered by who is blocked. For each, four lines and then stop:

```
#<n> — <author> opened it
what:    <one line: what the PR changes>
history: <one line: reviews so far, whose turn, how long it has sat>
propose: <one line: exactly what you will do if he says go>
```

Then wait. `vai` / `ok` / `procedi` / `si` means execute that proposal and move
to the next PR without re-asking. Anything else is a conversation about that PR:
answer it, adjust the proposal, ask again. Never present the next PR before the
current one is settled — the whole point of the format is that he holds one
decision in his head at a time.

### With `batch=N`

The four-line block does **not** degrade into a summary: it repeats. Print all
N blocks separated by a blank line, then ask **once** — one `AskUserQuestion`,
one question, `multiSelect: true`, one option per proposal (`#1145 vai`,
`#1128 vai`, …). Ticked means go, unticked means skip, and "Other" is there for
*"il 2 sì ma senza toccare i test"*. A typed answer (`1 vai, 2 no, 3 vai`, or
`tutte vai`) is accepted just the same — the box is the convenience, not the
protocol.

**A conversation about one does not hold up the others.** The clean `vai`
proposals start immediately; the one he wants to discuss returns at the head of
the next batch. Otherwise the batch buys nothing.

### What can run in parallel, and what cannot

**Never hand an approved batch straight to N agents.** Build the conflict graph
over the approved set, take its connected components, and run the components in
parallel while the members of one component run in sequence, in queue order.
Two PRs conflict when any of these holds:

- **they touch the same file** — intersect `gh pr diff <n> --name-only`;
- **they are stacked** — one's `baseRefName` is the other's `headRefName`
  (`gh pr view <n> --json baseRefName,headRefName`); the chain is sequential
  base→head;
- **they meet on the same issue** — intersecting `closingIssuesReferences`;
- **one of them is an A1 merge or an A3 realign** on a base the other shares.
  A merge into the base invalidates every `mergeStateStatus` read a moment
  ago, so **a merge or a realign runs alone**;
- they would push the same head branch.

**Unknown means sequential.** Say the grouping before launching, one line per
group:

> gruppo 2 (sequenziale): #1145 → #1128, toccano entrambe
> `gnrpy/gnr/web/gnrbaseclasses.py`

Every agent that touches a working tree runs with `isolation: "worktree"`, one
per PR, and never `git stash`: worktrees share a single stash stack, so a stash
in one agent surfaces in another. Use a patch file.

### When one of them fails

Each agent returns `{n, status: ok|failed, what, why}`. The report is **per
item, never aggregate** — there is no "batch completato" line: four items with
one failure is three successes and one failure, said in four rows. A failure
does not stop the others, but inside a **sequential** group it aborts the rest
of that group, reported as *"#1128 non tentata: #1145 è fallita"*.

**`basta` / `stop` / `per ora ok` ends the lane, and that is a normal ending.**
Go straight to Closing with what stands: what Lane A did, what was settled in
Lane B, and — the part that makes stopping halfway clean — **the PRs never
reached, in queue order**, so picking this up later starts where it left off.
An unanswered proposal counts as the same thing: do not keep pushing the queue
at him.

`propose` must be a single concrete action, not a menu: *approve with a note
about X*, *request changes on the CI failure*, *answer cgabriel that the
traceback is outside the diff and open an issue for the real finding*. If you
cannot compress it to one line, you have not finished analysing it.

Say plainly when a proposal is not yours to execute — marking a draft ready,
merging somebody else's PR, assigning an issue somebody else holds. Those rows
end with what he should do, and you move on.

## Verifying before you propose

A PR description is a claim, not evidence. Almost every real defect found with
this skill came from one move: taking a sentence from the description — "previous
behaviour is unchanged", "falls back to `url()`", "so it can be configured with
S3" — and going to find the function that would have to make it true. Roughly
half the time it is not true, and the author had no idea. Reading only the diff
cannot find these: the diff is locally consistent, and the lie is in the seam
between the diff and everything else.

Read the whole diff first, then run the moves that apply.

**Trace "no behaviour change" to the callee's default.** When a PR starts passing
a parameter that was previously omitted, the claim rests entirely on whether the
explicit value equals the callee's default. Read the signature. Thirty seconds,
and it either confirms the PR or sinks it.

**Check a deletion's justification before it destroys working code.** "This
example cannot work", "this mechanism was dropped" — go find the mechanism. Read
the function that would have to honour it, and grep whether the identifiers the
deleted code binds exist anywhere else in the repository. A deletion justified by
a wrong claim is the least recoverable kind of mistake in a review.

**Check the name resolves in the registry the code will actually use.** Codebases
accumulate parallel lookup tables with overlapping-but-different key sets. Find
the function that resolves, and read *which* table it consults — not which one
the concept belongs to. When the lookup returns `None` instead of raising, the
failure surfaces later as an `AttributeError` on `None`.

**Check the error branch can fire, and that what it reports can vary.** A guard
that enriches its message with context is worthless if the only state that
triggers it is the state where that context is empty.

**A new column is not new behaviour for old rows.** For any added column a code
path now keys off: what is its value on rows predating the migration, and did the
old path ever populate what the new path requires? "NULL" and "no" means existing
records silently fall out of the feature — highest severity, because tests pass,
CI is green, and it only appears in production data.

**When a mechanism is swapped, compare semantics, not call sites.** Replacing a
purpose-built matcher with a hand-rolled `LIKE`, or a permission engine with a
substring test, changes what matches. Unanchored substring matching gives false
positives on longer values (`admin` matches `superadmin`) and silently drops
whatever rule syntax the original understood. Every call site still compiles.

**Follow a value to where it is written, not to where it is set.** A generated
default injected for one path and carried into another is how an update ends up
rewriting a primary key. When a branch order changes, re-ask what the *other*
branch was preparing the data for.

**A shared fixture makes a config change repo-wide.** Before calling a config
edit local, grep for the instance or fixture name: a test base class naming one
instance means every suite inheriting it now loads whatever was added. Compare
the PR's CI against its base's — red on both is inherited, red only here is this
PR's delta, four-of-four red is not flakiness.

**Before calling an unfamiliar idiom wrong, grep for it.** Framework-specific
expression syntax looks arbitrary until you find the twenty other places using
it. Often the PR is *fixing* the broken form and the version that looks normal to
you is the bug. This applies to attribute access on a wrapper class too: read
whether `__getattr__` exists and whether `__setattr__` does — a read that
delegates and a write that does not is a real defect and looks like neither.

**Conversely, check the plumbing connects.** A localization entry does nothing if
the label is a bare string with no translation marker: the PR ships a dead entry
and thinks it shipped a translation.

**Put a ratchet under test rather than reading it.** When a PR claims a check
fails in both directions, break it both ways and watch it fail. Two experiments,
and it either holds or the whole series resting on it does not.

## Distrust tests until you read the fixture

"48 tests pass" is a claim like any other.

Read what the fixture builds. A fresh temp file per run makes a test idempotent
for free; a fixed, persistent database name that is never dropped does not. A
test that inserts a row and mutates it without cleanup passes once and then
collides with its own leftovers — and when the collision is on the very unique
index the PR is about, the test reproduces the bug against itself on the second
run.

Check the tests exercise the real thing. Mocks standing in for
insert/query/commit prove nothing about a change to insert/query/commit.

**Run the new tests against the base**, not only against the branch. Tests that
pass on the base too are pinning nothing — and the half of the fix they leave
unpinned is exactly where the regression will come back. Say which half.

A fixture that calls `pytest.skip` when its dependency is missing is a check that
protects nothing in the environment where it skips. Say where it skips.

## Blast radius lives outside the repo

For a framework repo, the code that breaks is in the applications. Local grep
cannot see them; the cross-repo index can. Search without a repo filter for the
attributes, methods and conventions the PR changes.

Look especially for **the application-level attribute that shadows the core
one** — apps routinely define their own `main_*` variant injecting
high-specificity CSS or config, sometimes resolved per record at runtime, while
core reads its own differently-named attribute. A PR measuring with the core
attribute while the app renders with its own produces a silent, systematic
mismatch no test in the framework repo can see.

Run it in both directions, because honesty requires both:

- who is **exposed** — call sites with no override, on the default path the PR changes;
- who is **shielded** — call sites already overriding the hook it rewrites.

The shielded set is usually large and it shrinks the blast radius. Report it. A
review that lists only the scary half is not more rigorous, it is less accurate,
and it burns your credibility for the findings that are real.

Then state the **direction** of the error. A size estimate too small clips
content and corrupts pagination; too large only wastes space. Wrong in the safe
direction is a different severity from wrong in the dangerous one, and saying
which is what makes the finding actionable.

## Two PRs, one file

Work out which hunk is duplicated and which parts are complementary before
recommending anything. The common pattern is two halves of the same bug at
different levels of the same call path, plus one small hunk both authors wrote
independently.

Compare hunk positions before claiming a conflict: three PRs in one 4000-line
file, hundreds of lines apart, do not conflict, and saying so is worth as much as
finding one that does. A modify/delete pair, on the other hand, always conflicts
however small the modification.

Then: let the smaller, already-approved one land first, and say in a comment on
the other what the rebase will do and which version of the shared hunk wins and
why. That comment is worth more than the review itself, because it stops the next
reviewer working out the overlap from scratch.

**A question addressed to somebody who is not a reviewer is a question nobody
will answer.** When a PR is parked on a decision, check that the person who owns
the decision is actually on the PR — and reach them where they are.

## Writing what you post

English, in the repo's voice, no AI or tool attribution anywhere — not in
reviews, comments, commits, PR bodies or issues.

Use `--body-file`; bodies contain backticks, code blocks and tables that shell
quoting will mangle.

Lead with what the PR gets right and name the design choice that was correct —
not as decoration, but because a reviewer who has clearly understood the intent
gets his objections taken seriously. Then the objection, with `file:line`
evidence and, where it is small, the exact replacement line.

Say what you did **not** verify, in its own line. A review that silently implies
a browser pass it never did is worse than one that names its own perimeter.

Choose the state by consequence, not by how much you found:

- a one-line fix to something that makes the feature unreachable, or a red CI
  traced to this PR's own delta — `--request-changes`: cheap to fix, expensive
  to ship;
- a correct change with a wide, documented, opt-out-able behaviour change — a
  `--comment` with the question, not a block;
- a test-hygiene problem on correct production code — approve with the note.
  Blocking correct work over a fixture is disproportionate.

```bash
gh pr review <n> --approve         --body-file <f>
gh pr review <n> --request-changes --body-file <f>
gh pr review <n> --comment         --body-file <f>
gh pr view <n> --json reviewDecision,mergeStateStatus,reviews    # a body-file typo fails quietly
```

**Findings you will not fix here belong in an issue, not in a review body.** A
paragraph in a thread is lost by the next push; an issue survives. Open it with
the mechanism, the line references, and — the part that saves the most time — the
things you checked that turned out *not* to be true, so nobody re-investigates
them.

**Record a withdrawn review request.** Dropping a reviewer is fine when the code
has been read by somebody and the request has sat untouched, but a reviewer who
finds his request silently gone learns to stop trusting the queue. Comment saying
who was dropped, since when, and who has approved in his place.

```bash
gh api repos/<owner/repo>/issues/<n>/timeline \
  --jq '.[]|select(.event=="review_requested")|{at:.created_at,who:.requested_reviewer.login}'
gh pr edit <n> --remove-reviewer <login>
```

On a PR with no review at all, dropping the reviewer means the code goes in
unread. Do not propose it; the PR needs a reviewer who will answer.

## Corrections

When new evidence flips a verdict you already gave — an approval that becomes a
question, a "keep my version" that becomes "keep theirs", a queue picture that
was wrong because a field went unread — say so plainly in one sentence and
correct the public record too, with a comment on the PR. A stale plan left
standing in a thread will be followed by somebody.

## Closing

Report against the goal, not as a diary of what you did.

1. **What Lane A settled** — a table, with the real check numbers, and which
   issues closed with each merge. Say when a merge closed no issue because none
   was linked: the traceability gap survives the merge.
2. **What is waiting on somebody else** — grouped by person, not by PR, so he can
   chase one name instead of re-reading a list.
3. **What is still his** — the only part that matters. Each with the one action
   it needs. If this list is empty, say so plainly: nothing is left on his plate.

If Lane A did nothing on its first pass, say that too — it means everything
remaining needed him, which is itself the answer to "do I have anything to do".

Never close with a PR silently unaccounted for. Every PR that entered the loop
appears in exactly one of the three lists, including the ones you deliberately
left alone and why.

## Publish to the review desk

**The desk must react to what the loop does.** After every action that
changes the queue — a merge, an answered review, a realign — and again at
the end of the loop:

- remove the settled PR's rows from `grid.blocks` in
  `~/.local/state/git-workflow/<owner>__<repo>.json` (a merged PR must
  disappear from the dashboard, not sit there looking pending), or re-run
  the queue read and re-export the whole grid per pr-triage §10;
- refresh `analysis`/`next` for what moved, and put the full text of any
  review drafted but not sent in that PR's `draft` key;
- one feed line per action (`notify.py`), in plain words;
- **say which PRs you are on, before you start on them**, so the desk puts the
  needle on those rows instead of leaving the user to read the feed:

```bash
# one at a time
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --pr <n> --working "cosa stai facendo su questa, in una riga"

# a batch: all of its rows glow, not just the first
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --batch 1145,1128,1059 --working "in parallelo, un worktree per PR"
```

  While a batch is live, `--pr <n> --working "…"` **refines that one item** and
  leaves the set standing: per-PR progress reaches the desk without collapsing
  three glowing rows back to one. Move the marker as the loop moves, and when
  the queue is empty drop it.

  **One request per loop, not per item.** The ▶ button's lock is `run:pr-loop`
  and stays a single request however wide the batches — closing it per item
  would re-arm the button mid-loop. What a batch changes is the report, which
  must name every item:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/server/notify.py --repo <owner/repo> \
  --done run:pr-loop "2 merge (#1145 #1059), 1 riallineo (#1102), 1 fallita (#1128: conflitto in un file che la base ha riscritto)"
```

  That command closes the loop's request (unlocking the ▶ pr-loop button and
  showing the outcome in its place) and clears the highlight. Use `--failed`
  only when **nothing** was accomplished: a loop that merged two and lost one
  did its job and says so in the report. A marker nobody updates for fifteen
  minutes is dropped by the desk on its own — a row left glowing after the loop
  died reads as work in progress, which is worse than no highlight — but that
  is a backstop, not a substitute.


## How to talk about the lanes

"Lane A" and "Lane B" are this file's internal names — the user should
never have to decode them. In chat, in feed notifications and in reports
say what they are: *azioni automatiche* (i merge delle tue PR approvate, le
risposte banali, i riallinei — fatte da solo) and *le PR che richiedono te,
una alla volta*. Use the lane names only when the user uses them first.
