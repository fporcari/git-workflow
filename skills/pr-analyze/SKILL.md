---
name: pr-analyze
description: Analyze ONE pull request the pr-run way — read the whole diff, verify the description's claims against the code, read the threads — and return the Lane B block (what / history / propose) plus any draft worth posting. Read-only, never posts or pushes. Used headless by the review-desk dashboard's Analyze button, or from chat on a single PR.
---

# PR analyze — one PR, the pr-run Lane B chunk

Read-only. This skill produces the decision block; acting on it is a separate,
explicitly authorized step. **Never** post, push, merge, edit, assign or
resolve anything here.

Input: a PR number and a repo (`owner/repo`). Work from `gh` alone — `gh pr
view`, `gh pr diff`, `gh api` — so no local checkout is required.

## 1 · Gather

```bash
gh pr view <n> --repo <owner/repo> --json title,body,author,isDraft,baseRefName,mergeStateStatus,reviewDecision,reviewRequests,reviews,closingIssuesReferences,headRefOid
gh pr diff <n> --repo <owner/repo>
gh api repos/<owner/repo>/pulls/<n>/comments --jq '.[]|{id,path,line,user:.user.login,body}'
gh api repos/<owner/repo>/pulls/<n>/reviews --jq '.[]|{user:.user.login,state,body,commit_id}'
gh pr checks <n> --repo <owner/repo>
```

## 2 · Verify

The full playbook is the pr-run skill's sections **"Verifying before you
propose"**, **"Distrust tests until you read the fixture"** and **"Blast
radius lives outside the repo"** — read them from
`${CLAUDE_PLUGIN_ROOT}/skills/pr-run/SKILL.md` (or the sibling
`../pr-run/SKILL.md` relative to this file) and run the moves that apply.
The core discipline: the description is a claim, not evidence — take each
sentence the correctness rests on and go read the function that would have
to make it true.

## 3 · The block

Compress to the pr-run Lane B format. `propose` is ONE concrete action, not a
menu — if it cannot be one line, the analysis is not finished.

## 4 · Output — strict JSON, nothing else in the final message

The final message must be exactly one JSON object (no fences, no prose):

```json
{"n": 1152,
 "what": "one line: what the PR changes",
 "history": "one line: reviews so far, whose turn, how long it has sat",
 "propose": "one line: exactly what will be done on a go-ahead",
 "draft": "full text of the comment/review to post, or null",
 "verified": ["what was actually checked this session"],
 "not_verified": ["what was not checked, named honestly"]}
```

## 5 · Publish to the review desk

Before the final message, merge into
`~/.local/state/git-workflow/<owner>__<repo>.json` (create dir/file if
missing, preserve other keys):

```json
{"prs": {"<n>": {"analysis": "<what + the key finding>",
                  "next": "<propose>", "draft": "<draft or omit>"}}}
```
