"""Contract tests for Mock API itself (no Docker / n8n required).

These DO NOT award the student's 60 functional points.  Student-facing tests
will test the n8n Webhook on a later project stage.
"""

import http.client
import importlib.util
import json
from pathlib import Path
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("n8n_lab_server_stage3", ROOT / "mock-api" / "server.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class MockAPIContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class IsolatedHandler(api.MockHandler):
            store = api.MockStore()
        cls.server = api.ThreadingHTTPServer(("127.0.0.1", 0), IsolatedHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.assertEqual(self.request("POST", "/__test__/reset", {})[0], 200)

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            status = response.status
            content_type = response.getheader("Content-Type")
            decoded = json.loads(response.read().decode("utf-8"))
            return status, decoded, content_type
        finally:
            connection.close()

    def register(self, **overrides):
        record = {
            "requestId": "REQ-1024", "userId": 451, "userName": "Иван Иванов",
            "department": "IT", "type": "technical", "priority": "high",
            "description": "Не удается авторизоваться", "assignedGroup": "Technical Support",
            "slaHours": 1, "status": "registered",
        }
        record.update(overrides)
        return self.request("POST", "/requests", record)

    def calls(self):
        return self.request("GET", "/__test__/calls")[1]["calls"]

    def records(self):
        return self.request("GET", "/__test__/records")[1]["records"]

    def test_health_and_content_type(self):
        status, result, content_type = self.request("GET", "/health")
        self.assertEqual((status, result["stage"]), (200, 3))
        self.assertIn("application/json", content_type)
        self.assertEqual(self.calls(), [])

    def test_known_users(self):
        for user_id, department in [(451, "IT"), (452, "Finance"), (453, "Security")]:
            with self.subTest(user_id=user_id):
                code, body, _ = self.request("GET", f"/users/{user_id}")
                self.assertEqual(code, 200)
                self.assertEqual(body["id"], user_id)
                self.assertEqual(body["department"], department)
        self.assertEqual(len(self.calls()), 3)

    def test_unknown_user(self):
        code, body, _ = self.request("GET", "/users/9999")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "USER_NOT_FOUND")
        self.assertEqual(self.calls()[0]["status"], 404)

    def test_registration_and_lookup(self):
        status, body, _ = self.register()
        self.assertEqual(status, 201)
        self.assertEqual(body, {"recordId": "rec-000001", "requestId": "REQ-1024", "status": "registered"})
        self.assertEqual(len(self.records()), 1)
        record = self.records()[0]
        self.assertEqual(record["description"], "Не удается авторизоваться")
        self.assertEqual(record["assignedGroup"], "Technical Support")
        code, found, _ = self.request("GET", "/requests/REQ-1024")
        self.assertEqual(code, 200)
        self.assertEqual(found, record)

    def test_unknown_request(self):
        code, body, _ = self.request("GET", "/requests/REQ-MISSING")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"]["code"], "REQUEST_NOT_FOUND")

    def test_mock_does_not_deduplicate(self):
        self.register()
        self.register()
        self.assertEqual(len(self.records()), 2)
        self.assertNotEqual(self.records()[0]["recordId"], self.records()[1]["recordId"])
        code, result, _ = self.request("GET", "/requests/REQ-1024")
        self.assertEqual(code, 200)
        self.assertEqual(result["recordId"], "rec-000001")

    def test_registration_fails_without_fields(self):
        code, result, _ = self.request("POST", "/requests", {"requestId": "REQ-X"})
        self.assertEqual(code, 400)
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.records(), [])
        self.assertEqual(self.calls()[0]["status"], 400)

    def test_registration_fails_for_bool_ints_and_bad_status(self):
        for overrides in [
            {"userId": True}, {"userId": "451"}, {"userId": -1},
            {"slaHours": 0}, {"slaHours": 1.5}, {"status": "pending"},
            {"priority": "  "},
        ]:
            with self.subTest(overrides=overrides):
                code, result, _ = self.register(**overrides)
                self.assertEqual(code, 400)
                self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.records(), [])

    def test_first_two_503_then_success(self):
        code, _, _ = self.request("POST", "/__test__/failures", {
            "method": "POST", "path": "/requests", "statuses": [503, 503], "repeatLast": False,
        })
        self.assertEqual(code, 200)
        self.assertEqual([self.register()[0] for _ in range(3)], [503, 503, 201])
        self.assertEqual([c["status"] for c in self.calls()], [503, 503, 201])
        self.assertEqual(len(self.records()), 1)

    def test_permanent_error(self):
        self.request("POST", "/__test__/failures", {
            "method": "POST", "path": "/requests", "statuses": [503], "repeatLast": True,
        })
        self.assertEqual([self.register()[0] for _ in range(4)], [503] * 4)
        self.assertEqual(self.records(), [])

    def test_permanent_400_is_not_successful(self):
        self.request("POST", "/__test__/failures", {
            "method": "POST", "path": "/requests", "statuses": [400], "repeatLast": True,
        })
        code, result, _ = self.register()
        self.assertEqual(code, 400)
        self.assertEqual(result["error"]["code"], "SIMULATED_FAILURE")
        self.assertEqual(self.records(), [])

    def test_simulate_user_lookup_failure(self):
        self.request("POST", "/__test__/failures", {
            "method": "GET", "path": "/users/451", "statuses": [503],
        })
        self.assertEqual(self.request("GET", "/users/451")[0], 503)
        self.assertEqual(self.request("GET", "/users/451")[0], 200)
        self.assertEqual(self.request("GET", "/users/452")[0], 200)
        self.assertEqual([c["status"] for c in self.calls()], [503, 200, 200])

    def test_simulate_lookup_record_failure(self):
        self.register()
        self.request("POST", "/__test__/failures", {
            "method": "GET", "path": "/requests/REQ-1024", "statuses": [503],
        })
        self.assertEqual(self.request("GET", "/requests/REQ-1024")[0], 503)
        self.assertEqual(self.request("GET", "/requests/REQ-1024")[0], 200)

    def test_fault_configuration_is_replaced(self):
        self.request("POST", "/__test__/failures", {
            "method": "POST", "path": "/requests", "statuses": [503], "repeatLast": True,
        })
        self.request("POST", "/__test__/failures", {
            "method": "POST", "path": "/requests", "statuses": [400], "repeatLast": False,
        })
        self.assertEqual([self.register()[0] for _ in range(2)], [400, 201])

    def test_malformed_fault_configuration_is_rejected(self):
        bad_configs = [
            {"method": "POST", "path": "/requests", "statuses": []},
            {"method": "POST", "path": "/requests", "statuses": [200]},
            {"method": "POST", "path": "/requests", "statuses": [True]},
            {"method": "POST", "path": "/__test__/reset", "statuses": [503]},
            {"method": "GET", "path": "/health", "statuses": [503]},
            {"method": "POST", "path": "/requests", "statuses": [503], "repeatLast": "yes"},
        ]
        for config in bad_configs:
            with self.subTest(config=config):
                code, result, _ = self.request("POST", "/__test__/failures", config)
                self.assertEqual(code, 400)
                self.assertEqual(result["error"]["code"], "INVALID_FAILURE_CONFIG")

    def test_reset_clears_data_faults_calls_and_record_id(self):
        self.register()
        self.request("POST", "/__test__/failures", {
            "method": "POST", "path": "/requests", "statuses": [503], "repeatLast": True,
        })
        code, body, _ = self.request("POST", "/__test__/reset", {})
        self.assertEqual((code, body), (200, {"status": "reset"}))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.records(), [])
        self.assertEqual(self.register()[1]["recordId"], "rec-000001")

    def test_call_timestamps_and_fields(self):
        self.request("GET", "/users/451")
        self.register()
        calls = self.calls()
        self.assertEqual([c["path"] for c in calls], ["/users/451", "/requests"])
        for call in calls:
            self.assertEqual(set(call), {"method", "path", "status", "timestampMs"})
            self.assertIsInstance(call["timestampMs"], int)
            self.assertGreater(call["timestampMs"], 1700000000000)

    def test_unknown_endpoint(self):
        self.assertEqual(self.request("GET", "/not-a-path")[0], 404)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
