---
name: pr-triage
description: Show every open PR the user is involved in, split into blocks by the kind of work it needs — mergeable now, trivial action, reviews he owes, people to chase (grouped per person, copy-pasteable), and the calls only he can make — each row carrying number, date, author, what it is, what is to be done, and whether /pr-loop would handle it unattended, read from the GitHub fields rather than by reading diffs. Explicit invocation ONLY - use exclusively when the user invokes /pr-triage by name; never trigger it on your own, however well a request seems to match. Read-only itself, it does not stop at the grid: it hands over to pr-loop, which acts on it, after one question about what to leave alone.
---

# PR triage

Read-only. Answers "what is on my plate" from the GitHub fields, never the diffs.
Acting on what it finds is `pr-loop`.

**A queue is finished when the user has nothing left to do himself** — not when
every PR is merged. Every verdict is written from his side: whose move is it, and
if it is his, which move.

**Never does:** read diffs, verify, write review bodies (that is `pr-loop`); post,
push, rebase, merge, assign, withdraw a request; judge a PR he is not involved
in; claim searched or verified for anything not done this session — an unread
field is `not checked`, not a guess. When a cell needs a diff read to fill
honestly, write what the fields say and let `pr-loop` find out.

Every rule below was learned by getting it wrong. `WHY.md` in this directory has
the case behind each one — **read the relevant section when you are about to skip
a rule, when one looks wrong for this repo, or when the user asks why.** Do not
read it to run a normal triage.

## 1 · Date the session

```bash
date +%F && gh repo view --json name --jq .name
```

`set_session_title` on `self` with `PR triage · <repo> · <YYYY-MM-DD>`. Read the
date from `date`, never from memory or the context block. Leave a hand-written
title alone.

## 2 · Read the queue

**If a desk handed you a `rows` path** (the `triage` event carries it), the
queue is already downloaded AND the deterministic work is already done. That
file holds:

| key | what it already is |
|---|---|
| `queue` | the rows, with `todo`/`state`/`autorun` computed (§7) |
| `grid` | the five blocks of §5, already partitioned |
| `chase` | the §6 blocks, per person, oldest first, with the dates |
| `gates` | the §3 gate of every base: who may land, approvals, conversation resolution, CODEOWNERS |
| `shortlist` | the ten issues worth reading |

**Do not recompute any of it.** Reproducing that grid costs ~28k tokens of
input and a whole turn, to re-derive a mapping the desk computes in 0.07 ms.
Your job on a desk-driven triage is only what the fields cannot answer:

1. the rows the computed grid marks `asks` — those need the diff or the
   review read (§4's DIRTY/UNSTABLE rules, and the CODEOWNERS per-path
   question when `gates.<base>.per_path` is true);
2. **what each PR is FOR**, one line, in the user's language — the desk shows
   the raw title and asks for this per row (the `explain` event), so write it
   only for the rows you were asked about;
3. §8's three repo-level findings, which are patterns nobody can look up.

Then export what you added, merged into the grid you were given — the desk
shows a model-verified grid in place of its computed one, and says which of
the two the user is looking at.

```bash
jq '.queue' <rows path> > /tmp/rows.json
jq -r '.[]|"\(.n)\t\(.created)\t\(.author)\tdraft=\(.draft)\t\(.merge)/\(.decision)\treq=\(.req|join(","))\tunres=\(.unresolved)/\(.threads)\tlast=\(if .last then "\(.last.t[0:10]) \(.last.who) \(.last.ch)" else "-" end)"' /tmp/rows.json
```

Same shape, same fields, one `jq` instead of ~6s of GitHub search. A `merge`
of `null` means the desk's second phase had not landed yet: press ⟳ on the
desk, or fall back to the calls below for that field alone.

Otherwise, read it yourself — **one call, not four**: `involves:<login>` is a
superset of author, assignee, commenter, mentions, review-requested and
reviewed-by, so one search resolves every relationship the verdicts need.

```bash
R=<owner/repo>; U=<login>; S=${CLAUDE_PLUGIN_ROOT}/skills/pr-triage
G=${CLAUDE_PLUGIN_ROOT}/server/gql
gh api graphql -F query=@$G/pr_core.graphql \
  -f q="repo:$R is:open is:pr involves:$U" > /tmp/q_involves.json &
gh api graphql -F query=@$G/pr_mergestate.graphql \
  -f q="repo:$R is:open is:pr author:$U" > /tmp/q_merge.json &
wait
jq -n -f $S/queue.jq /tmp/q_involves.json > /tmp/rows_core.json
jq -s -f $S/mergein.jq /tmp/q_merge.json /tmp/rows_core.json > /tmp/rows.json
```

`mergeStateStatus` rides in its own call because it is the single most
expensive field GitHub serves — asking for it inside the queue search costs
more than the whole rest of the query — and the verdicts only read it for
your own PRs.

- Confirm `hasNextPage` is false; if not, follow `endCursor`.
- **Never loop per PR.** Everything the verdicts need is in `rows.json`. Open a
  single PR only for a `DIRTY` branch's conflicting paths or an `UNSTABLE` PR's
  failing job.
- On `HTTP 502`, lower `first:` — never fall back to a loop, never report a
  relationship as empty because its call failed.
- Ask who to skip; parked PRs are often known. Say which ones you excluded.

## 3 · Read the gate — once, before any verdict

**A desk hands you this already read**, in `gates.<base>` of the rows file:
`landers` (who may actually land — `null` means unrestricted), `can_land`,
`bypass` (an admin walking past `enforce_admins: false`), `approvals`,
`codeowners_required`, `codeowners_path`, `owners`, `per_path`,
`dismiss_stale`, `conversation_resolution`. Read it from there; the three
calls below are for a standalone run.

This is not academic: on a repo where one person cuts the releases, the
release bases carry a push restriction, and an approved CLEAN PR of the
user's own is **not his merge** — the field-only verdict says `A1 → merge it`
there and is wrong. Read `landers` before writing an `A1`.

```bash
gh api repos/<owner/repo>/branches/<default-branch>/protection
gh api repos/<owner/repo>/collaborators/<login>/permission --jq .permission
gh api "repos/<owner/repo>/contents/.github/CODEOWNERS?ref=<default-branch>" --jq '.content' | base64 -d
```

| field | what it changes |
|---|---|
| `required_approving_review_count` + `require_code_owner_reviews` | with CODEOWNERS, the *only* logins whose approval clears the gate. Everyone else's is a courtesy that moves nothing |
| `required_conversation_resolution` | when true, `unresolved > 0` blocks the merge on its own → that row is the author's move |
| `restrictions.users`/`.teams` | who may actually land a PR. Approval and permission to merge are different things |
| `enforce_admins: false` | an admin bypasses all of it. Then `BLOCKED` is a convention, not a wall — check his own permission, never infer it from role names in a memory file |

When CODEOWNERS has per-path rules rather than one catch-all, the eligible logins
differ per PR: match the rules against the paths that PR touches before writing
"one approval missing" — last matching pattern wins.

```bash
gh pr diff <n> --name-only | sed 's|/[^/]*$||' | sort -u
```

## 4 · Field rules

- **`isDraft` overrides everything.** Never "waiting on X" for a draft; it is
  waiting on its author to mark it ready.
- **A standing `reviewRequests` entry** is somebody asked who has not answered —
  but under a catch-all CODEOWNERS it carries no information about that person.
- **Empty `reviewRequests` is two opposite states**, and `reviews` separates them:
  with a submitted review, `APPROVED` + `CLEAN` means mergeable and forgotten;
  with `reviews` also empty it is **unreviewed** → `get a reviewer`.
- **An approval counts only on the current head.** With `dismiss_stale_reviews`, a
  `DISMISSED` entry counts for nothing.
- **`CLEAN` means nothing on an unprotected base** — read `base` first. Stacked
  PRs: mark the chain, name the PR they sit on, judge them by the eventual base.
- **`CHANGES_REQUESTED` is sticky, not an inbox.** `last` decides whose turn it
  is.
- **`last` is the answer to the question this skill exists to ask.** Whoever spoke
  last owns the ball; if that is anyone but the user, the row is his move,
  whatever `reviewDecision` says. It merges all three channels a reviewer can
  speak on — issue comment, submitted review, and inline diff comment
  (`ch:"inline"`), the last two invisible in the issues timeline.
- **`unresolved > 0`** is the author's move when conversation resolution is on.
- **`UNSTABLE`**: compare against the base's own run — red on both is inherited,
  red only here is this PR's delta, four-of-four red is not flakiness.
- **`DIRTY`** is a fact about the branch, not a verdict on the work. What it
  conflicts on decides who fixes it.

```bash
gh pr checks <n>
git merge-tree --write-tree --name-only <pr-head> <base> | tail -5
```

## 5 · The output — five blocks, cheapest first

Not one table. Each block gets its own heading and its own small table, so he can
stop at the one he has time for. Same six columns everywhere:

| # | date | author | what it is | what is to be done | autorun |
|---|---|---|---|---|---|

- **#** as a markdown link · **date** = `createdAt` plus the age in days once past
  the turnaround (4h critical, 24h high, 48h normal, a week low); `updatedAt` is
  useless, any push resets it · **author**, `me` for his own · **what it is** in
  one line in the user's language, no verdict · **what is to be done** from the
  vocabulary below · **autorun** = what `pr-loop` does with it.

Never write an `A1`/`A2`/`A3` whose gates you have not checked. When a gate needs
a diff read, the honest cell is `asks`.

1. **Da mergiare subito** — `A1` only. Usually empty: say so in one line rather
   than printing an empty table.
2. **Azione banale** — `A2`/`A3`: the named one-line edit, the mechanical
   realign. Work needing no thought, only a go. Never put a design decision here.
3. **Review da fare** — `review it`, `re-review it`. Above his own chasing,
   because here he is somebody else's blocker.
4. **Da sollecitare, per persona** — the fenced blocks of §6 and *nothing else*:
   no table, no per-PR line, no count. A chase is a message to paste, not a task.
5. **Solo tue** — `decide with`, an `answer <login>` that is a judgement call, a
   draft to open or finish. Last because it is slowest, not least important.

Everything else goes under the five as prose, one line per person, so he can see
nothing was dropped.

## 6 · The chasing blocks

One fenced block per person, numbers on one line, ready to paste:

```
@<login> — 7 PR ferme dal 6/7 agosto, tutte con la tua approvazione mancante:
#1027 #1044 #1045 #1050 #1055 #1056 #1057
```

```bash
jq --argjson owners '["<owner1>","<owner2>"]' --arg me <login> \
   --arg today "$(date +%F)" --argjson days 2 --arg trunk <default-branch> \
   -f ${CLAUDE_PLUGIN_ROOT}/skills/pr-triage/chase.jq /tmp/rows.json
```

Returns `chase` per person (each split into `ritardatari` and `mai_iniziate`),
`excluded` with a reason per row, and `courtesy_dropped` as counts. It drops
courtesy names, ignores approvals when deciding who spoke last, and targets the
author of a live `CHANGES_REQUESTED` rather than the codeowner.

**The gate — one wrong number discredits the whole message**, including the rows
that were right. Read `excluded` before printing; its reasons are what you tell
the user instead. A row ships only if all four hold:

- the person chased owes the next move — `last` is the user or nobody, and the
  live conversation is not with a different reviewer;
- it is past the promised turnaround;
- nothing else blocks it — not a draft, no unresolved threads, not stacked, not
  `DIRTY`;
- he was actually asked. An eligible codeowner never requested is
  `get a reviewer`, not a chase.

**A chase never overrides one of his own moves** — assign the other blocks first
and subtract; `chase.jq` classifies on approvals alone and cannot know.

**Print no block for an empty set and never pad one.** Rows that fail the gate go
in the prose tail, unfenced: a fence promises the contents are ready to send. If
every set is empty, say in one line that there is nobody to chase today.

**Label each block.** `ritardatario` = everyone else answered, one required name
left, work finished. `mai iniziata` = nobody approved either, so it is a PR that
never entered anybody's queue, not a person being slow. An empty `ritardatario`
set is itself a finding — say why.

## 7 · The verdict vocabulary

Closed set. Anything else is `needs a look - <the one unclear thing>` with `asks`.

| what is to be done | when | autorun |
|---|---|---|
| `merge it` | his PR, not draft, `APPROVED`, zero standing requests, approvals on current head, `CLEAN` on a protected base | `A1` |
| `answer the review` | a reviewer's `CHANGES_REQUESTED` is last and the ask is one named local edit | `A2` |
| `answer the review` | same, but the ask needs analysis first | `asks` |
| `answer <login>` | a reviewer's `COMMENTED` is last and asks a question | `asks` |
| `decide with <login>` | same, but it is a design choice between named alternatives | `yours` |
| `realign with the base` | `DIRTY`, conflict on a shared changelog/version/lock file or disjoint additions | `A3` |
| `realign with the base` | `DIRTY`, conflict in a file the base itself rewrote | `asks` |
| `review it` | he is a requested reviewer | `asks` |
| `re-review it` | he left `CHANGES_REQUESTED` and the author has answered since | `asks` |
| `get a reviewer` | `reviews` empty and no useful standing request | `asks` |
| `resolve the threads` | `unresolved > 0` with conversation resolution on | `asks` |
| `mark ready` | `isDraft`, work looks complete | `yours` |
| `finish it` | `isDraft`, work genuinely unfinished | `yours` |
| `chase <login>` | everything done, one named person has not answered — including a required reviewer clearing newer PRs while this one starves | `-` (block 4 only, never counted) |
| `waiting on <login>` | not his move at all | `-` |
| `blocked on #<n>` | stacked, or waiting for another PR to settle | `-` |

## 8 · Under the blocks — three repo-level findings

Report once, in prose, never as rows.

**Uncovered paths in CODEOWNERS.** Name the paths with no matching rule and no
catch-all, and propose the rule: that fixes every future PR.

**Issues a PR closes but nobody owns**, plus the reverse defect — a body naming an
issue in prose while `closes` is empty, so the link never formed:

```bash
jq -r '.[]|.n as $p|.author as $a|.closes[]
       |select((.assignees|length)==0)|"\($p)\t\($a)\tissue #\(.issue) unassigned"' /tmp/rows.json
```

**A reviewer starving the old rows while clearing the new ones.** Never report
"one person is the standing request on most rows" — under a catch-all CODEOWNERS
that is the config file, not news. Report the *shape* of his attention instead:

```bash
gh search prs --repo <owner/repo> --reviewed-by <login> --limit 60 --json number,state
```

Reviewing nothing anywhere → genuinely absent, raise it or re-route the rule.
Reviewing the recent while an older batch sits → newest-first, and those rows are
starved: name that batch by number. Either way the verdict is `chase <login>`,
never `waiting on <login>` — but it stays out of the counts.

## 9 · Closing — never stop at the blocks

Three counts, one line each:

- **unattended** (`A1`/`A2`/`A3`), listed by number so he can veto one;
- **brought to him** one at a time (`asks`);
- **only his** (`yours`) — drafts, design calls. **Never the chase rows**: say in
  the same line that they are excluded and sit in block 4.

Then **one** question and only this one: anything on the unattended list to leave
alone? Then invoke `pr-loop` with the vetoes, in the same session — not "you can
now run it". `vai`, `ok`, an empty answer or silence all exclude nothing.

Two cases end the run instead, and both must be said out loud:

- **nothing is his move** — every row `waiting on` or `-`. The queue needs nothing
  from him; there is nothing to drain.
- **he asked for the blocks only** — `report-only`, "solo la griglia", or a
  refusal at the veto question.

Nothing after the handover: a summary that re-narrates the blocks wastes the scan
they were built for.

## 10 · Publish to the review desk

Before the handover, export the run so the `review-desk` dashboard's Triage
tab shows THIS grid — the desk renders nothing there until a real triage has
run. Merge into `~/.local/state/git-workflow/<owner>__<repo>.json` (create
the directory, preserve unrelated keys):

```json
{
  "session": "PR triage · <repo> · <YYYY-MM-DD>",
  "grid": {
    "generated": "<ISO timestamp>",
    "blocks": [
      {"title": "Da mergiare subito",
       "rows": [{"n": 1027, "date": "2026-07-29", "author": "me",
                  "what": "<one line, user's language>",
                  "todo": "merge it", "autorun": "A1"}]},
      {"title": "Azione banale", "rows": []},
      {"title": "Review da fare", "rows": []},
      {"title": "Solo tue", "rows": []},
      {"title": "In attesa di altri", "rows": []}
    ]
  },
  "chase": {"<login>": "<the exact fenced block of §6, verbatim>"},
  "prs": {"<n>": {"analysis": "<in italiano: what it is + the verdict's reason>",
                   "next": "<what is to be done>"}}
}
```

The five §5 blocks map onto `grid.blocks` in the same order (block 4's chase
messages go under `chase`, per person, only the ones that passed the §6
gate). User-facing strings (`what`, `analysis`) in Italian; the verdict
vocabulary stays as-is. Overwrite `grid`, `chase` and `session` wholesale:
stale triage is worse than none.

**Triggered from the desk** (a `{"kind": "triage"}` inbox event): run
report-only — blocks, findings, export — and skip the §9 handover question;
the user is driving from the dashboard and will press run himself.
