# git-workflow eval suite

Routing cases only (tag `routing`, Italian variants tagged `it`): every skill in this plugin acts through `gh`, which the eval sandbox has no access to, so what is measured is whether the right skill fires for a request and whether the explicit-only ones (issue-triage, pr-triage, the loops) stay quiet when the user merely asks a question.

Run from the plugin root (`plugins/git-workflow`) after `claude update` >= 2.1.269.

Cheap first pass, one arm:

    claude plugin eval . --runs 1 --ablation none --no-publish

Full run with the no-plugin baseline (default ablation), 3 runs per case:

    claude plugin eval . --max-cost-usd 5 --no-publish

`tool_used: Skill` graders are plugin-fired indicators under ablation, not part of the score; the `min: 0 / max: 0` ones carry `arm: both` so a skill firing where it must not costs points in both arms.
Results land in `results/` (gitignored).

Outside an interactive terminal (CI, Desktop background shells) add `--trust-plugin`: the first run in a terminal trusts the directory once instead.
