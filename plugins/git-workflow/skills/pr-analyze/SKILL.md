---
name: pr-analyze
description: Analyze ONE pull request — identify its author and the problem it solves, reconstruct its review history, verify its claims against the code and propose one next action for confirmation. Read-only, never posts or pushes. Used headless by the review desk or directly in chat.
---

# PR analyze — one verified decision

Read-only. This skill produces the decision block; acting on it is a separate,
explicitly authorized step. **Never** post, push, merge, edit, assign or
resolve anything here.

Input: a PR number and a repo (`owner/repo`). Work from `gh` alone, so no
local checkout is required. **No triage is required first**: a PR the user
already knows — somebody flagged it, it came up in chat — is analyzed
directly, even while a triage runs in a background agent. `<PLUGIN_ROOT>` is
the plugin directory containing this skill's `skills/`, `refs/` and `server/`
siblings.

## 1 · Gather once

Start the GraphQL snapshot and the diff concurrently when the host supports
parallel read-only calls:

```bash
gh api graphql -f owner=<owner> -f name=<repo-name> -F number=<n> -F query=@<PLUGIN_ROOT>/server/gql/pr_analysis.graphql
gh pr diff <n> --repo <owner/repo>
```

The snapshot contains the PR opening, linked issue context, author, requested
reviewers, general comments, reviews with commit ids, resolved and unresolved
review threads, and the latest commit's checks. A true `hasNextPage` means the
corresponding history is incomplete: fetch the next page when it matters
to the decision, otherwise name the gap in `not_verified`.

Read the whole diff, not only its summary. Do not repeat fields already present
in the snapshot with `gh pr view` or the REST review endpoints.

## 2 · Establish the decision

Read `<PLUGIN_ROOT>/refs/pr-verification.md` and run the checks that apply;
`pr-verification-WHY.md` beside it has the case behind each one, for when you
are about to skip a check.

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

## 3 · Choose one proposal

`propose` is one concrete action, not a menu. If it cannot be one line, the
analysis is not finished. Draft the exact English review/comment only when that
action needs text posted on the PR; otherwise `draft` is `null`.

## 4 · Output

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

Do not say merely that a draft exists: when attached to the desk, say that the
English draft is visible there. Outside the desk, include the draft after the
decision block so the user can inspect what the proposal would post. A `vai`,
`ok`, `procedi` or `si` authorizes exactly the displayed proposal, not any
other action.

## 5 · Publish to the review desk

In an attached interactive session, merge into
`~/.local/state/git-workflow/<owner>__<repo>.json` (create dir/file if
missing, preserve other keys):

```json
{"prs": {"<n>": {"author": "<provider login>",
                  "problem": "<problema + soluzione, in italiano>",
                  "history": "<cronologia + a chi tocca, in italiano>",
                  "analysis": "<problema + storia, in italiano>",
                  "next": "<la proposta, in italiano>",
                  "draft": "<the English draft, or omit>"}}}
```

In headless mode, do not write any file. The caller validates the final JSON
and performs the atomic merge into desk state.
