"""Cross-host packaging invariants, without either host installed."""

import json
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
REPOSITORY = Path(__file__).resolve().parents[4]


class Packaging(unittest.TestCase):
    def manifest(self, host):
        return json.loads((PLUGIN / (".%s-plugin" % host) / "plugin.json").read_text())

    def test_host_manifests_share_identity_and_base_version(self):
        claude = self.manifest("claude")
        codex = self.manifest("codex")
        self.assertEqual(claude["name"], codex["name"])
        self.assertEqual(claude["version"].split("+", 1)[0],
                         codex["version"].split("+", 1)[0])

    def test_both_marketplaces_resolve_to_the_shared_plugin(self):
        claude = json.loads(
            (REPOSITORY / ".claude-plugin" / "marketplace.json").read_text())
        codex = json.loads(
            (REPOSITORY / ".agents" / "plugins" / "marketplace.json").read_text())
        claude_path = REPOSITORY / claude["plugins"][0]["source"]
        codex_path = REPOSITORY / codex["plugins"][0]["source"]["path"]
        self.assertEqual(claude_path.resolve(), PLUGIN)
        self.assertEqual(codex_path.resolve(), PLUGIN)

    def test_every_shared_skill_has_codex_metadata(self):
        for skill in (PLUGIN / "skills").iterdir():
            if skill.is_dir():
                self.assertTrue((skill / "SKILL.md").is_file(), skill.name)
                self.assertTrue((skill / "agents" / "openai.yaml").is_file(),
                                skill.name)

    def test_shared_skills_do_not_depend_on_host_runtime_symbols(self):
        forbidden = ("CLAUDE_PLUGIN_ROOT", "$ARGUMENTS", "AskUserQuestion",
                     "spawn_task", "set_session_title", "set_thread_title",
                     "create_thread", "spawn_agent")
        for path in (PLUGIN / "skills").glob("*/SKILL.md"):
            text = path.read_text()
            for token in forbidden:
                self.assertNotIn(token, text, "%s in %s" % (token, path.name))

    def test_background_delegation_is_not_a_codex_task(self):
        runtime = (PLUGIN / "refs" / "runtime.md").read_text()
        desk = (PLUGIN / "skills" / "review-desk" / "SKILL.md").read_text()
        self.assertIn("Codex's collaboration/subagent tool", runtime)
        self.assertIn("Codex task/thread", runtime)
        self.assertNotIn("Codex: a task", desk)

    def test_the_title_tool_has_a_host_mapping(self):
        """Host-neutral skills need the mapping to live somewhere, and
        Claude's title tool is deferred: unnamed is never loaded."""
        runtime = (PLUGIN / "refs" / "runtime.md").read_text()
        self.assertIn("mcp__ccd_session_mgmt__set_session_title", runtime)
        self.assertIn("ToolSearch", runtime)

    def test_claude_commands_are_only_thin_host_wrappers(self):
        """Both triages run their model half in a background subagent and
        title their session: a wrapper that allows neither contradicts the
        skill it loads."""
        for name in ("pr-triage", "issue-triage"):
            wrapper = (PLUGIN / "commands" / ("%s.md" % name)).read_text()
            self.assertIn("CLAUDE_PLUGIN_ROOT", wrapper, name)
            self.assertIn("skills/%s/SKILL.md" % name, wrapper, name)
            allowed = next(line for line in wrapper.splitlines()
                           if line.startswith("allowed-tools:"))
            for tool in ("Agent", "mcp__ccd_session_mgmt__set_session_title"):
                self.assertIn(tool, allowed, "%s in %s" % (tool, name))

    def test_analysis_schema_is_valid_json(self):
        schema = json.loads(
            (PLUGIN / "server" / "schemas" / "pr-analysis.json").read_text())
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("author", schema["required"])
        self.assertIn("problem", schema["required"])

    def test_the_headless_analysis_cannot_write_through_gh(self):
        """`gh api` accepts -X POST: an allowlist that grants it grants the
        merge too, and this agent is read-only."""
        import sys
        sys.path.insert(0, str(PLUGIN / "server"))
        import jobs
        self.assertNotIn("Bash(gh api:*)", jobs.READ_TOOLS)
        self.assertIn("Bash(gh api graphql:*)", jobs.READ_TOOLS)
        self.assertIn("Bash(gh api -X GET repos/*/compare/*:*)",
                      jobs.READ_TOOLS)
        self.assertIn("Bash(gh api -X GET repos/*/contents/*:*)",
                      jobs.READ_TOOLS)

    def test_pr_analysis_has_a_verified_procedural_fast_path(self):
        text = (PLUGIN / "skills" / "pr-analyze" / "SKILL.md").read_text()
        for rule in ("procedural refresh", "reviewed commit oid",
                     "never trust a merge commit message", "Do not fetch",
                     "tree is never evidence"):
            self.assertIn(rule, text, rule)
        self.assertIn("git diff <reviewed-oid>..<head-oid> --", text)

    def test_the_decision_block_is_not_a_copy_paste_fence(self):
        """The three labels are what he scans; a fence flattens them and
        offers a copy nobody wants."""
        for name in ("pr-analyze", "pr-loop"):
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text()
            self.assertIn("**Proposta** ·", text, name)
            self.assertIn("code fence", text, name)

    def test_the_worktree_protocol_has_one_home(self):
        traps = (PLUGIN / "refs" / "worktree-traps.md").read_text()
        for rule in ("PYTHONPATH", "GENRO_GNRFOLDER", "git stash",
                     "Do NOT fork", "Fixes #"):
            self.assertIn(rule, traps, rule)
        for name in ("pr-loop", "issue-loop", "issue-work"):
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text()
            self.assertIn("refs/worktree-traps.md", text, name)
            self.assertNotIn("worktrees share one stash stack", text,
                             "%s copies the traps instead of citing them" % name)

    def test_the_issue_decision_blocks_are_not_fences_either(self):
        for name in ("issue-analyze", "issue-loop"):
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text()
            self.assertIn("**Proposta** ·", text, name)
            self.assertIn("code fence", text, name)

    def test_the_wide_bug_mode_states_its_gate(self):
        """One go-ahead over many PRs is safe only because a bug rarely
        carries a decision — so the mode must exclude the ones that do, out
        loud."""
        text = (PLUGIN / "skills" / "issue-loop" / "SKILL.md").read_text()
        self.assertIn("bugfix", text)
        for gate in ("no open decision", "DEFECT", "SINGLE-PHASE",
                     "said out loud"):
            self.assertIn(gate, text, gate)

    def test_no_skill_writes_a_shortlist_or_a_grid_back(self):
        """Both are the desk's: computed on every read (shortlist) or
        published on the press (grid). A model copy of either was one more
        thing to keep in sync, and it drifted."""
        for name in ("issue-triage", "issue-desk", "pr-triage", "review-desk"):
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text()
            self.assertNotIn('"shortlist": {', text, name)
            self.assertNotIn('"grid": {', text, name)

    def test_the_verification_checklist_keeps_its_cases(self):
        why = (PLUGIN / "refs" / "pr-verification-WHY.md").read_text()
        self.assertIn("claim, not evidence", why)
        for name in ("pr-analyze", "pr-loop"):
            self.assertIn("pr-verification-WHY.md",
                          (PLUGIN / "skills" / name / "SKILL.md").read_text(), name)

    def test_analysis_query_carries_the_decision_context(self):
        query = (PLUGIN / "server" / "gql" / "pr_analysis.graphql").read_text()
        for field in ("author", "bodyText", "reviewThreads",
                      "closingIssuesReferences", "statusCheckRollup"):
            self.assertIn(field, query)
        probe = (PLUGIN / "server" / "gql" / "pr_probe.graphql").read_text()
        for field in ("headRefOid", "reviews", "reviewThreads",
                      "statusCheckRollup"):
            self.assertIn(field, probe)
        self.assertNotIn("bodyText", probe)

    def test_desks_stay_attached_unless_the_user_opts_out(self):
        """The desk is the remote, the launching chat is the workplace: every
        click except triage is executed where the user reads it."""
        for name in ("pr-desk", "issue-desk"):
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text()
            self.assertIn("attached by\ndefault", text, name)
            self.assertIn("Detached (opt-in)", text, name)
            self.assertIn("chatdesk.py listen", text, name)
            self.assertIn("Monitor(", text, name)
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", text, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
