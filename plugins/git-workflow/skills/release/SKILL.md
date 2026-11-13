---
name: release
description: >-
  Cut a release in a repository whose merges the user handles himself. Every
  open PR labelled `release` is merged into a
  fresh `release_<ddmmyyHHMM>` branch cut from the default branch, one PR goes
  from it to the default branch and, on the user's go-ahead, lands with a merge
  commit, so the default branch moves once per release.
disable-model-invocation: true
---

# Release

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

Invoking this skill is the user's statement that he handles the merges of the
repository: the default branch moves only through release branches. Pull
requests exist to work in parallel, the user's and the collaborators' alike,
and the `release` label is the approval: the user puts it on any PR, his own
included, since Forgejo never lets an author approve his own. This skill
collects the labelled ones; `pr-loop` is where they get labelled.

## 1. What goes in

```sh
gw repo default-branch
gw api repos/{repo}/branch_protections
gw label ensure release --color 0e8a16 --description "Approved: goes into the next release branch"
gw pr list --state open
```

In: the rows labelled `release`, `base` equal to the default branch, `draft`
false, in `n` order. None: stop, there is nothing to release. A labelled PR on
another base, or still a draft, is listed as left out with its reason. No other
label counts, `needs-verification` included: `release` is the user's word.

A protection rule on the default branch that asks for an approval
(`required_approvals` above 0) or keeps a merge whitelist without him would
refuse the release PR at the end. Say it now, with the setting to change:
required approvals 0, merge whitelist with him alone. The release is built
all the same; only the merge waits for the setting.

## 2. The branch

Build it in a throwaway worktree, so the user's checkout stays where it is:

```sh
name=release_$(date +%d%m%y%H%M)
git fetch origin
git worktree add -b "$name" <scratch>/release origin/<default>
```

Then each PR, in order:

```sh
git fetch origin +refs/pull/<n>/head:refs/remotes/pr/<n>
git -C <scratch>/release merge --no-ff --no-edit -m "Merge PR #<n>: <title>" pr/<n>
```

The repository's commit hook runs on every merge commit; never `--no-verify`.
A hook that fails: `git -C <scratch>/release merge --abort`, and the PR is
left out with the hook's output. A PR left out stays open and labelled for the
next release.

A conflict is read before anything is decided
(`git -C <scratch>/release diff --name-only --diff-filter=U`):

- **Generated files.** The repository's CLAUDE.md declares them, one line each,
  `release-generated: <glob>... <- <command>`. Take the incoming side with
  `git restore --theirs -- <files>` and `git add` them: the rebuild below
  replaces them anyway.
- **Prose**, Markdown and the like: show the user each conflicting hunk, both
  sides and the resolution you would make, usually both sides kept in the
  file's own order. Resolve it only on his go-ahead; otherwise abort and leave
  the PR out.
- **Anything else**: `merge --abort`, the PR is left out with its conflicting
  files. That conflict belongs to the PR, against the default branch once this
  release has landed (`pr-loop` A3).

Resolve line by line, never by string index (`pr-loop` A3 says why), then
commit the merge with its message unchanged.

After the last PR, run each declared `release-generated` command once in the
release worktree and commit what it changed as `Rebuild generated files`.

Nothing merged: remove the worktree and the branch, and stop.

## 3. The tests

Before anything leaves the machine, run the repository's tests on the release
worktree, the way its CLAUDE.md says, including what it says about running
them from a worktree. A red test is shown to the user with its output, next
to whether it already fails on the default branch; the push waits for his
word. A CLAUDE.md that names no tests: say so in the report, never invent a
command.

## 4. The release PR

```sh
git -C <scratch>/release push -u origin "$name"
gw pr view <n>        # each included PR, for its `closes`
gw pr create --title "$name" --head "$name" --assignee @me --body-file <f>
```

The body, in English and with no tool attribution:

- one line per included PR: `- #<n> <title> (@<author>)`;
- `Fixes #<i>` for every issue an included PR closes: the release PR is the
  one that reaches the default branch, so it is the one that closes them;
- the PRs left out, each with its reason.

## 5. The merge

Show the user the release: the PR link, what went in, what was left out and
why, the issues it closes, the tests. Ask right before merging; the go-ahead
covers this release only.

```sh
gw pr merge <release> --delete-branch
```

Never `--squash`: the included PRs read as merged only while their own head
commits reach the default branch unchanged.

A merge the service refuses is never retried with `force_merge` or any other
admin bypass. Give the user the link and the refusal, and name the setting
step 1 checked: the label is the approval, so the default branch wants no
required approval and a merge whitelist with him alone.

Then read each included PR back with `gw pr view <n>`. GitHub marks it merged;
Forgejo does when the repository has "autodetect manual merge" on. One still
open is closed by hand, pointing at the release:

```sh
gw pr comment <n> --body "Shipped in <name> (#<release>)."
gw api repos/{repo}/pulls/<n> -X PATCH -f state=closed
```

Delete the head branches of the user's own included PRs; the collaborators'
belong to them. Then `git worktree remove <scratch>/release --force`.

## 6. Report

In Italian, short: the release name and PR link, the PRs that went in, the ones
left out with their reason, the conflicts resolved and how, the tests, the
issues closed, and any included PR still open after the merge.
