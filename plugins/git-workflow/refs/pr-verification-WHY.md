# Why each verification check exists

`pr-verification.md` is the checklist; this is the case behind it. Read a
section when you are about to skip its check, when one looks wrong for this
repo, or when the user asks why. Do not read it to run a normal review — it is
the same relationship `WHY.md` has with pr-triage.

A PR description is a claim, not evidence. Almost every real defect found this
way came from one move: taking a sentence from the description — "previous
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
   issues closed with each merge, naming any merge that closed none.
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
  needle on those rows instead of leaving the user to read the feed, and close
  the loop's request when it ends:

```bash
python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> \
  --pr <n> --working "cosa stai facendo su questa, in una riga"

python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> \
  --batch 1145,1128,1059 --working "in parallelo, un worktree per PR"

python3 <PLUGIN_ROOT>/server/notify.py --repo <owner/repo> \
  --done run:pr-loop "2 merge (#1145 #1059), 1 riallineo (#1102), 1 fallita (#1128: conflitto in un file che la base ha riscritto)"
```

**The semantics of those flags** — why a batch marker is a set, why marking one
member refines it instead of collapsing it, why the loop closes ONE request
however wide its batches, and when `--failed` is the wrong word — are in
`<PLUGIN_ROOT>/skills/review-desk/SKILL.md` §3, *Say which rows you are
on* and *Close the request when you are done*. That file is the protocol; this
one only uses it.
