---
name: pr-analyze
description: Analyze ONE pull request — identify its author and the problem it solves, reconstruct its review history, verify its claims against the code and propose one next action for confirmation. Read-only, never posts or pushes. Used headless by the review desk or directly in chat.
---

# PR analyze — one verified decision

Read-only. This skill produces the decision block; acting on it is a separate,
explicitly authorized step. **Never** post, push, merge, edit, assign or
resolve anything here.

Input: a PR number and a repo (`owner/repo`). The provider snapshot and diff
are authoritative. Exact local Git objects may accelerate code reads when the
requested commit already exists; a checkout is never required and the working
tree is never evidence about the PR. **No triage is required first**: a PR the
user already knows — somebody flagged it, it came up in chat — is analyzed
directly, even while a triage runs in a background agent. `<PLUGIN_ROOT>` is
the plugin directory containing this skill's `skills/`, `refs/` and `server/`
siblings.

## 1 · Probe before gathering

When the launch prompt carries `<desk_context>`, consume it first. Its `probe`
is a fresh provider read; its `row` is the desk's normalized context. Do not
request fields either already contains. `cached_problem` is reusable without
new semantic verification. `previous_problem` is reusable only after the
procedural comparison below proves that behaviour did not change since
`previous_problem_head`.

Without a fresh desk probe, fetch the lightweight one before the diff:

```bash
gh api graphql -f owner=<owner> -f name=<repo-name> -F number=<n> -F query=@<PLUGIN_ROOT>/server/gql/pr_probe.graphql
```

First decide whether this is a **procedural refresh**: an approval or review was
invalidated only because a merge, rebase or push changed the head, and the next
action might simply be to request the same reviewers again.

If `cached_problem` exists, it already belongs to the current head. When the
probe is complete, no unresolved feedback needs reading and its fields settle
the history and next action, reuse that problem and stop without buying the
whole diff. A new unresolved thread or an incomplete connection falls through.

For that case, take the reviewed commit oid from the snapshot and compare it
with the current `headOid`. If both commits already exist locally, use exact
objects only:

```bash
git cat-file -e <oid>^{commit}
git diff <reviewed-oid>..<head-oid> --
```

Otherwise use one narrowly scoped read-only provider compare request:

```bash
gh api -X GET repos/<owner>/<repo>/compare/<reviewed-oid>...<head-oid> --jq '[.files[]|{filename,status,additions,deletions,patch}]'
```

Inspect the actual delta; never trust a merge commit message as proof. If the
delta does not alter behaviour owned by the PR (for example it only integrates
the base), current checks pass and no new unresolved feedback exists, stop
there and propose requesting the reviewers again **only when a reusable problem
statement is available**. Do not repeat the original call path, CODEOWNERS
search, tests or cross-repository blast-radius search. Record those deliberately
skipped checks in `not_verified`.

For `previous_problem`, reusable means that `previous_problem_head` is the
reviewed oid used as the comparison baseline. Otherwise it is only a hint and
cannot enable the fast path.

If the delta changes feature behaviour, contains an ambiguous conflict
resolution, lacks a usable reviewed commit oid or reusable problem, or leaves
any relevant claim uncertain, fall through to full verification.

## 2 · Gather once for full verification

Only after the probe fails to settle the decision, start the complete GraphQL
snapshot and the whole PR diff concurrently when the host supports parallel
read-only calls:

```bash
gh api graphql -f owner=<owner> -f name=<repo-name> -F number=<n> -F query=@<PLUGIN_ROOT>/server/gql/pr_analysis.graphql
gh pr diff <n> --repo <owner/repo>
```

The snapshot contains the PR opening, linked issue context, author, requested
reviewers, comments, reviews with commit ids, review threads and latest checks.
A true `hasNextPage` means the corresponding history is incomplete: fetch the
next page only when it matters, otherwise name the gap in `not_verified`.

Read the whole diff, not only its summary. Do not repeat fields already present
in the desk evidence or snapshot with `gh pr view` or REST review endpoints. Do
not rerun a query merely to reshape its output. Batch independent code reads
and searches instead of discovering them one serial command at a time.

## 3 · Establish the full decision

Read
`<PLUGIN_ROOT>/refs/pr-verification.md` and run the checks that apply;
`pr-verification-WHY.md` beside it has the case behind each one, for when you
are about to skip a check.

When `cached_problem` is present, preserve it and verify only the stale history
or proposal facts. Do not retrace its semantic call path.

For full verification, prefer exact local objects when `git cat-file -e
<headOid>^{commit}` succeeds: read with `git show <headOid>:<path>` and compare
with `git diff`, never with the checked-out working tree. Do not fetch. If the
object is absent, read the exact remote object with
`gh api -X GET repos/<owner>/<repo>/contents/<path>?ref=<headOid> -H
'Accept: application/vnd.github.raw+json'`. Add a read only when it resolves a
concrete claim or call-path edge, and batch independent reads.

Produce four distinct facts:

- `author`: the provider login, never inferred from prose or commits;
- `problem`: the user-visible or operational problem and how this PR addresses
  it; for a pure refactor, say explicitly that there is no behaviour change;
- `history`: the meaningful chronology, including whose turn it is now, open
  threads, requested reviewers, checks and how long it has been waiting;
- `propose`: one concrete next action, with the review state or message it
  entails. If it is not the user's action to take, say whose action it is.

The PR title and body are claims. Prefer the linked issue and verified call path
when they disagree. Do not turn a file list into the problem statement.

## 4 · Choose one proposal

`propose` is one concrete action, not a menu. If it cannot be one line, the
analysis is not finished. Draft the exact English review/comment only when that
action needs text posted on the PR; otherwise `draft` is `null`.

## 5 · Output

**Language**: everything shown to the user — the desk state's `analysis` and
`next`, the chat report — is written in **Italian**; only `draft` stays in
English, because it is text meant to be posted on the PR.

**Never show raw JSON to the user.** In headless mode (the desk's standalone
mode or an agent caller), the final message is exactly one JSON object, with no
fences or prose. The schema is:

```json
{"n": 1152,
 "author": "provider login",
 "problem": "one line: the problem and how the PR addresses it",
 "history": "one line: reviews so far, whose turn, how long it has sat",
 "propose": "one line: exactly what will be done on a go-ahead",
 "draft": "full text of the comment/review to post, or null",
 "verified": ["what was actually checked this session"],
 "not_verified": ["what was not checked, named honestly"]}
```

In chat, show exactly this Italian decision block — **as text, never inside a
code fence**. A fence turns it into something to copy, kills the word-wrap
(long lines break mid-word instead of flowing), and the labels stop standing
out; here they are the whole point, because he reads the three lines and
answers. Same ban on the fence's cousins: never the English field names
(`problem:` / `history:` / `propose:` are the JSON's, not the user's) and
never alignment indentation to make values line up under a label — an
indented continuation renders as code and wraps unreadably. A long value is
one plain paragraph after its bold label, nothing more.

(the fence below delimits the template — your output has no fence)

```markdown
**#<n>** — aperta da @<author>

**Problema** · <problem>
**Storia** · <history>
**Proposta** · <propose>

Procedo con questa proposta?
```

One line each, in that order, with the label in bold. Nothing before the
block, nothing after the question.

Do not say merely that a draft exists: in detached desk mode it is returned in
the structured result and the server makes it visible there. Outside the desk,
include the draft after the decision block so the user can inspect what the proposal would post. A `vai`,
`ok`, `procedi` or `si` authorizes exactly the displayed proposal, not any
other action.

## 6 · Publish to the review desk

In detached desk mode, do not write any file. Return the structured JSON from
the launch prompt; the Python server validates it and merges it with the
`analysis_key`. For a legacy direct integration that explicitly supplies an
`analysis_key`, merge the result into
`~/.local/state/git-workflow/<owner>__<repo>.json` (create dir/file if
missing, preserve other keys):

```json
{"prs": {"<n>": {"author": "<provider login>",
                  "problem": "<problema + soluzione, in italiano>",
                  "history": "<cronologia + a chi tocca, in italiano>",
                  "analysis": "<problema + storia, in italiano>",
                  "analysis_key": "<the event's analysis_key>",
                  "next": "<la proposta, in italiano>",
                  "draft": "<the English draft, or omit>"}}}
```

In headless mode, do not write any file. The caller validates the final JSON
and performs the atomic merge into desk state.
