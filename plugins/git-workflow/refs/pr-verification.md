# Verifying a pull request

A PR description is a claim, not evidence. Read the whole diff first, then
take each sentence the correctness rests on and inspect the function that
would have to make it true. The important defects usually live at the seam
between a locally consistent diff and the code around it.

Run only the checks that apply. `pr-verification-WHY.md`, next to this file,
carries the case behind each one — read the relevant section when you are
about to skip a check or one looks wrong for this repo.

## Claims and call paths

**Trace "no behaviour change" to the callee's default.** When a PR starts
passing a parameter that was previously omitted, compare the explicit value
with the callee's default.

**Check a deletion's justification before it destroys working code.** Find
the mechanism said to be absent and grep whether the identifiers used by the
deleted code exist elsewhere.

**Check the name resolves in the registry the code actually uses.** Read the
resolver and its lookup table. Parallel registries with overlapping keys are
a common source of delayed `None` failures.

**Check an error branch can fire and its context can vary.** A richer error is
dead code when the only triggering state makes that context empty.

**Treat a new column and old rows separately.** Find its value on rows that
predate the migration and whether the old write path populated what the new
read path requires.

**Compare semantics when a mechanism is swapped.** A hand-rolled `LIKE`,
substring check or replacement matcher may compile at every call site while
changing what matches.

**Follow a value to where it is written.** A generated default prepared for
one branch can leak into another and rewrite persistent data.

**Treat a shared fixture as repo-wide.** Grep for the fixture or instance
name, then distinguish failures already present on the base from failures
introduced by the PR.

**Grep before calling an unfamiliar idiom wrong.** Read framework wrappers
and delegation methods, especially asymmetric `__getattr__`/`__setattr__`
behaviour.

**Check that plumbing connects.** A localization entry, registry item or
configuration key is inert unless the consuming path can reach it.

**Put a ratchet under an important test claim.** Break the asserted behaviour
in both directions and confirm the test fails both ways.

## Tests and fixtures

"Tests pass" is a claim like any other. Read what the fixture builds and
whether it cleans up. A persistent database or fixed resource can pass once
and then collide with its own leftovers.

Check that tests exercise the changed mechanism. Mocks standing in for
insert/query/commit do not prove a change to insert/query/commit.

Run new tests against the base when feasible. A test that also passes there
pins no regression; say which part remains unprotected.

A fixture that skips when a dependency is absent protects nothing in that
environment. Name the skipped perimeter.

## Blast radius outside the repository

For a framework repository, application code may carry the real blast
radius. Search the available cross-repository index without a repository
filter for changed attributes, methods and conventions.

Look for application-level hooks that shadow core hooks. Report both:

- who is **exposed** because it uses the changed default path;
- who is **shielded** because it already overrides that path.

Then state the direction of harm. An underestimate that clips content or
corrupts pagination has a different severity from an overestimate that only
wastes space.

If no cross-repository index is available, name that in `not_verified`; do
not imply that a repository-local grep measured the external blast radius.
