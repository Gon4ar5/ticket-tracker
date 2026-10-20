import tempfile
import unittest
from pathlib import Path

from worker.runner import build_prompt, codex_child_environment, read_spec, safe_slug


class AgentRunnerHelpersTest(unittest.TestCase):
    def test_codex_child_environment_drops_worker_token_and_keeps_other_values(self):
        environment = codex_child_environment({
            "TICKET_AGENT_TOKEN": "worker-secret",
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp/user",
        })

        self.assertNotIn("TICKET_AGENT_TOKEN", environment)
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")
        self.assertEqual(environment["HOME"], "/tmp/user")

    def test_slug_is_branch_safe_and_bounded(self):
        self.assertEqual(safe_slug("TT-10: Search / AI"), "tt-10-search-ai")
        self.assertLessEqual(len(safe_slug("x" * 200)), 48)

    def test_reads_spec_only_from_inside_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "checkout"
            root.mkdir()
            (root / "spec.md").write_text("approved behavior", encoding="utf-8")
            self.assertIn("approved behavior", read_spec(root, "spec.md"))
            outside = root.parent / "outside-spec.md"
            outside.write_text("must not leak", encoding="utf-8")
            self.assertIn("not available inside", read_spec(root, str(outside)))
            self.assertNotIn("must not leak", read_spec(root, str(outside)))

    def test_prompt_contains_ticket_spec_and_operational_limits(self):
        prompt = build_prompt(
            {"key": "TT-0042", "title": "Add search", "description": "Find tickets",
             "acceptance_criteria": "Searches by title", "spec_ref": "docs/search.md"},
            "Follow AGENTS.md", "approved spec",
        )
        for expected in ["TT-0042", "Find tickets", "Searches by title", "approved spec",
                         "Do not merge", "deploy", "Follow AGENTS.md"]:
            self.assertIn(expected, prompt)


if __name__ == "__main__":
    unittest.main()
