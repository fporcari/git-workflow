# Why each rule in pr-triage exists

Every rule in `SKILL.md` was learned by getting it wrong in front of the user.
This file holds the case behind each one, so the skill itself can stay short.

**Read a section here when** you are about to skip the rule it explains, when the
rule looks wrong for the repo in front of you, or when the user asks why the
report says what it says. Do not read the whole file to run a triage.

---

## Reading the queue

**Do not loop over the PRs.** A per-PR `gh api` loop over a 48-PR queue is ~150
sequential HTTP round trips and several minutes. The four parallel GraphQL calls
were measured at 5-7 seconds over that same queue and cross-checked row-for-row
against the loop version: same rows, same last speakers. The loop is not a
fallback, it is the old bug.

**`HTTP 502` is not deterministic.** The same query passed, then failed, then
passed again on the same queue. It means the node budget was exceeded, so the fix
is smaller `first:` values — never a retry loop, and never reporting a
relationship as empty because its call died.

**`reviewed-by:$U` is a separate relationship for a reason.** A PR drops out of
`review-requested:$U` the moment you submit anything on it. If you left
`CHANGES_REQUESTED`, you are still the one who has to come back and re-read it,
and without this search that PR is invisible to the triage.

## Reading the state

**`isDraft` overrides everything.** A draft notifies no reviewer and cannot be
merged, so a draft with three standing review requests is not waiting on three
people. Printing it as "waiting on X" produced the most demoralising wrong
picture this skill has ever handed over: 27 PRs described as ignored by the
reviewers, 9 of which had never asked anybody.

**A standing request in a catch-all CODEOWNERS repo says nothing about the
person.** With `* @a @b`, GitHub auto-requests both on every PR the user opens.
Counting those entries and naming the winner is therefore a restatement of the
config file. This was printed as the headline finding on three consecutive runs —
24 of 27, then 37 of 45, then 35 of 48 — and produced no action any of the three
times. The user's father, the person being named, had already objected to it in
person before the third one went out.

**A `COMMENTED` review is invisible to every field.** It leaves `reviewDecision`
untouched, and GitHub clears the reviewer's standing request when he submits it.
So a PR where a reviewer asked a real question four days ago reads exactly like
one nobody has opened. Ten PRs were reported as waiting on the reviewer when the
ball had been the author's for days; the user found out from the reviewer, not
from the tool.

**The inline comment is the third channel, and it was missed even after that
fix.** A reviewer who annotates lines without submitting a review body leaves no
timeline event, no `reviewDecision`, no standing request. The fix for `COMMENTED`
read two channels out of three and still reported such a PR as untouched. `last`
merges all three because reading fewer is how the queue silently backs up.

**Read the branch protection before writing a verdict.** Three runs described the
user as blocked on 34 PRs without ever reading it. `develop` turned out to have
`required_conversation_resolution` on — an unresolved thread blocks the merge by
itself, which no other field hints at — push restricted to one person who is not
the user, and `enforce_admins: false` with the user an admin. He had merged 28 of
the previous 40 PRs himself by bypassing. Presenting him as powerless was simply
false.

**`CLEAN` means nothing on an unprotected base.** A PR targeting a feature branch
reports `CLEAN` with an empty `reviewDecision` because no review is *required*
there, not because it passed one. Stacked PRs are the usual case and reading
`CLEAN` as "ready" on one is wrong twice over.

**`CHANGES_REQUESTED` is sticky.** It stays lit until the reviewer submits again,
so on a second visit it says nothing about whose turn it is — the author may have
answered a week ago.

## The blocks

**One long table ordered by "who is blocked" is what this skill used to print.**
The user's words: the list is uncomfortable. The kinds of work were interleaved,
so he re-read forty rows to find the three he could act on. The blocks exist so
he can stop reading at the one he has time for.

**A chase is not a task on his plate.** Counting solleciti as `yours` put 25 in
the number he reads as his workload, when 23 of them were messages he could paste
in ten seconds and two were real work. The fenced block is the whole deliverable
for that kind of row.

**Never file a design decision under "azione banale".** Three PRs whose open
question was an architecture call — where to keep generated `.db` files, exception
handling in a legacy module, whether to widen a column instead of failing an
upgrade — are not one-line edits, and block 2 looking fuller is not worth
mis-describing them.

## The chasing gate

The user's own framing: *se sollecito e non c'è nulla da sollecitare faccio una
figura di merda.* He pastes the block, the recipient opens the first PR, finds
nothing to answer, and the entire message is discredited — including the rows
that were right. One wrong number costs more than twenty missing ones.

The first block printed for him contained 20 PRs and would have been exactly
that: four were one day old, and two had a live `CHANGES_REQUESTED` from a
*different* reviewer, so chasing the codeowner on them asked him to approve over
somebody else's open objection.

**Courtesy names must be dropped before grouping.** On that queue, 24 standing
requests belonged to one non-codeowner and 6 to another. Chasing either produces
an approval that clears nothing and wastes their afternoon.

**Two things a hand pass gets wrong every time**, both found by getting them
wrong in the same session:

- *An `APPROVED` does not hand the ball back to the author.* Reading "who spoke
  last" literally dropped a finished PR out of the chase because the approver
  happened to be the last speaker — the one row that most deserved to be in it.
- *A live `CHANGES_REQUESTED` decides the target.* The person to chase is the
  reviewer who asked for changes and now owes the re-review, not the codeowner
  whose approval is also pending.

**ritardatario vs mai iniziata.** They read identically in the fields and they
are opposite situations. Everyone else has answered and one required name is all
that is left — that is worth a direct message, because the work is finished and
one click is missing. Nobody has approved at all — that is not a person being
slow, it is a PR that never entered anybody's queue, and saying otherwise blames
a name for a queue that was never started.

**An empty `ritardatario` set is itself a finding.** On this queue it came out
empty at first because every approval on those rows came from somebody who was
never eligible to clear the gate.

## Repo-level findings

**Uncovered paths in CODEOWNERS.** A directory with no matching rule and no `*`
catch-all gets no codeowner and no automatic reviewer, so every PR confined to it
opens silently unassigned. Naming the paths fixes every future PR; assigning by
hand fixes only today's.

**An unassigned issue with an open PR against it reads as still-to-do** in every
issue list, because the workflow is assignment-based. PRs opened outside a triage
run carry no claim step at all, so this needs checking across the whole queue.

**A body naming an issue in prose while `closes` is empty** means the link never
formed. The issue is then invisible to this check and to everybody else's.

**Reviewer attention has a shape, and it decides the wording.** Reviewing nothing
anywhere for weeks means genuinely absent — raise it, or re-route the codeowner
rule. Reviewing the recent PRs while an older batch sits untouched means working
newest-first, and the oldest rows are starved. Nine PR numbers a person can be
pointed at is a move; "chase him, he holds 30" is not, and it is also unfair.

## The handover

**A grid nobody acts on is the failure mode this skill was split out of** — a
tidy picture, and then the user driving every move by hand anyway. That is why
the run is mandatory rather than suggested.

**The veto window exists because `pr-loop`'s Lane A merges and pushes without
asking again.** Somebody who invoked what he thinks of as a read-only triage must
see the unattended list once, before it runs.

## What this skill costs

The data is 5-7 seconds. Everything else is context and, downstream, `pr-loop`'s
real verification of each PR — worktree, linter, the new tests run against the
base as well, cross-repo greps. A 17-minute run got through one PR of Lane B.
That is the skill working as designed; the triage is not what makes it slow, and
keeping `SKILL.md` short is how it stays that way.
