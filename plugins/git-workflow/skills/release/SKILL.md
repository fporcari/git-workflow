---
name: release
description: >-
  Cut a release on a release-train repository, one whose CLAUDE.md carries the
  line `release-train: yes`. Every open PR labelled `release` is merged into a
  fresh `release_<ddmmyyHHMM>` branch cut from the default branch, one PR goes
  from it to the default branch and, on the user's go-ahead, lands with a merge
  commit, so the default branch moves once per release.
disable-model-invocation: true
---

# Release

Read `<PLUGIN_ROOT>/refs/runtime.md` first.

On a release-train repository the default branch moves only through release
branches. Pull requests exist to work in parallel, the user's and the
collaborators' alike, and the `release` label is the approval: the user puts it
on any PR, his own included, since Forgejo never lets an author approve his own.
This skill collects the labelled ones; `pr-loop` is where they get labelled.

## 0. The switch

From the repository's checkout:

```sh
grep -qx 'release-train: yes' CLAUDE.md
```

No match: stop and say the repository is not on the release train. The line is
the user's decision, taken in the repository's own CLAUDE.md; never infer it.

## 1. What goes in

```sh
gw repo default-branch
gw label ensure release --color 0e8a16 --description "Approved: goes into the next release branch"
gw pr list --state open
```

In: the rows labelled `release`, `base` equal to the default branch, `draft`
false, in `n` order. None: stop, there is nothing to release. A labelled PR on
another base, or still a draft, is listed as left out with its reason.

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
A conflict, or a hook that fails: `git -C <scratch>/release merge --abort`,
the PR is left out with the conflicting files or the hook's output, and the
next one goes on. A PR left out stays open and labelled for the next release.
Never resolve a conflict here: it belongs to the PR, against the default
branch once this release has landed (`pr-loop` A3).

Nothing merged: remove the worktree and the branch, and stop.

## 3. The release PR

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

## 4. The merge

Show the user the release: the PR link, what went in, what was left out and
why, the issues it closes. Ask right before merging; the go-ahead covers this
release only.

```sh
gw pr merge <release> --delete-branch
```

Never `--squash`: the included PRs read as merged only while their own head
commits reach the default branch unchanged.

Then read each included PR back with `gw pr view <n>`. GitHub marks it merged;
Forgejo does when the repository has "autodetect manual merge" on. One still
open is closed by hand, pointing at the release:

```sh
gw pr comment <n> --body "Shipped in <name> (#<release>)."
gw api repos/{repo}/pulls/<n> -X PATCH -f state=closed
```

Delete the head branches of the user's own included PRs; the collaborators'
belong to them. Then `git worktree remove <scratch>/release --force`.

## 5. Report

In Italian, short: the release name and PR link, the PRs that went in, the ones
left out with their reason, the issues closed, and any included PR still open
after the merge.
