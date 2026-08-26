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

    def test_shared_skills_do_not_depend_on_claude_runtime_symbols(self):
        forbidden = ("CLAUDE_PLUGIN_ROOT", "$ARGUMENTS", "AskUserQuestion",
                     "spawn_task")
        for path in (PLUGIN / "skills").glob("*/SKILL.md"):
            text = path.read_text()
            for token in forbidden:
                self.assertNotIn(token, text, "%s in %s" % (token, path.name))

    def test_claude_command_is_only_a_thin_host_wrapper(self):
        wrapper = (PLUGIN / "commands" / "issue-triage.md").read_text()
        self.assertIn("CLAUDE_PLUGIN_ROOT", wrapper)
        self.assertIn("skills/issue-triage/SKILL.md", wrapper)

    def test_analysis_schema_is_valid_json(self):
        schema = json.loads(
            (PLUGIN / "server" / "schemas" / "pr-analysis.json").read_text())
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("author", schema["required"])
        self.assertIn("problem", schema["required"])

    def test_analysis_query_carries_the_decision_context(self):
        query = (PLUGIN / "server" / "gql" / "pr_analysis.graphql").read_text()
        for field in ("author", "bodyText", "reviewThreads",
                      "closingIssuesReferences", "statusCheckRollup"):
            self.assertIn(field, query)


if __name__ == "__main__":
    unittest.main(verbosity=2)
