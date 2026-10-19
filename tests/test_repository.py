"""Publication safety checks for the public student repository.

These tests don't grade students' n8n implementation. They only verify that
educational source files and local documentation are consistent.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCUMENTS = [
    "README.md", "REPORT.md", "docs/SETUP.md", "docs/ASSIGNMENT.md",
    "docs/N8N_BASICS.md", "docs/API_CONTRACT.md", "docs/TEST_CASES.md",
    "docs/GRADING.md", "docs/SUBMISSION.md", "docs/TEST_RUNNER.md",
]
REQUIRED_WORKFLOWS = [
    "tutorial-basics.json", "tutorial-items-loops.json",
    "tutorial-webhook.json", "student-starter.json",
]


class RepositoryIntegrityTests(unittest.TestCase):
    def test_documents_present(self):
        for path in REQUIRED_DOCUMENTS:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), path)

    def test_local_markdown_links_resolve(self):
        for path in (ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))):
            text = path.read_text(encoding="utf-8")
            # Local Markdown links only. External URLs and anchors are excluded.
            for href in re.findall(r"\]\(([^\s)]+)\)", text):
                href = href.split("#", 1)[0].split("?", 1)[0]
                if not href or "://" in href or href.startswith(("mailto:", "data:")):
                    continue
                with self.subTest(source=path.name, href=href):
                    self.assertTrue((path.parent / href).exists(), f"{path}: {href}")

    def test_workflows_importable_and_no_credentials(self):
        for name in REQUIRED_WORKFLOWS:
            with self.subTest(workflow=name):
                raw = (ROOT / "workflows" / name).read_text(encoding="utf-8")
                workflow = json.loads(raw)
                self.assertFalse(workflow.get("active", False))
                self.assertTrue(workflow.get("nodes"))
                self.assertTrue(workflow.get("connections"))
                for node in workflow["nodes"]:
                    self.assertFalse(node.get("credentials"), f"Credential reference in {name}")

    def test_public_repo_does_not_contain_instructor_solution(self):
        self.assertFalse((ROOT / "instructor").exists())
        self.assertFalse((ROOT / "reference-baseline.json").exists())
        self.assertEqual(
            sorted(p.name for p in (ROOT / "workflows").glob("*.json")),
            sorted(REQUIRED_WORKFLOWS),
        )

    def test_functional_grading_weights(self):
        # Do not import run_tests here, since it also serves as a CLI; keep
        # the assertion simple and independent from the Mock API server.
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location("n8n_lab_runner", ROOT / "tests" / "run_tests.py")
        module = module_from_spec(spec)
        # run_tests uses dataclasses, whose runtime needs a registered module.
        import sys
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            self.assertEqual(len(module.BASE_CASES), 18)
            self.assertEqual(sum(case[2] for case in module.BASE_CASES), 60)
            self.assertEqual(len(module.EXPERT_CASES), 8)
        finally:
            sys.modules.pop(spec.name, None)


if __name__ == "__main__":
    unittest.main()
