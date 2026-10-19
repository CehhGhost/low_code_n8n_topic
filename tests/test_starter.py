"""Статическая проверка стартового workflow; не заменяет запуск n8n."""
import json
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "workflows" / "student-starter.json"

class TestStudentStarter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(PATH.read_text(encoding="utf-8"))
        cls.nodes = {node["name"]: node for node in cls.workflow["nodes"]}

    def test_webhook_contract(self):
        hooks = [n for n in self.nodes.values() if n["type"] == "n8n-nodes-base.webhook"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["parameters"]["path"], "support-request")
        self.assertEqual(hooks[0]["parameters"]["responseMode"], "responseNode")

    def test_workflow_is_starter_only(self):
        self.assertEqual(len(self.workflow["nodes"]), 4)
        self.assertNotIn("n8n-nodes-base.httpRequest", [n["type"] for n in self.nodes.values()])
        self.assertIn("TODO", str(self.workflow))

    def test_references_only_exist(self):
        for name, outputs in self.workflow["connections"].items():
            self.assertIn(name, self.nodes)
            for output in outputs["main"]:
                for conn in output:
                    self.assertIn(conn["node"], self.nodes)

if __name__ == "__main__":
    unittest.main()
