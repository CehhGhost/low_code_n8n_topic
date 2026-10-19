"""Проверки обучающего workflow и базовых endpoints без Docker.

НЕ проверяет выполнение workflow внутри n8n: для этого нужен Docker.
"""
import http.client
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lab_mock_api', ROOT/'mock-api'/'server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class MinimalAPITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),module.MockHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        conn.request(method,path)
        res=conn.getresponse()
        code=res.status
        content_type=res.getheader('content-type')
        obj=json.loads(res.read())
        conn.close()
        return code,content_type,obj

    def test_health(self):
        status,ct,obj=self.request('GET','/health')
        self.assertEqual(status,200)
        self.assertIn('application/json',ct)
        self.assertEqual(obj['status'],'ok')

    def test_fixed_users(self):
        for user_id,department in [(451,'IT'),(452,'Finance'),(453,'Security')]:
            with self.subTest(user_id=user_id):
                status,ct,obj=self.request('GET',f'/users/{user_id}')
                self.assertEqual(status,200)
                self.assertEqual(obj['id'],user_id)
                self.assertEqual(obj['department'],department)

    def test_missing_user(self):
        status,_,obj=self.request('GET','/users/9999')
        self.assertEqual(status,404)
        self.assertEqual(obj['error']['code'],'USER_NOT_FOUND')

    def test_stage3_requires_post_body(self):
        status,_,obj=self.request('POST','/requests')
        self.assertEqual(status,400)
        self.assertEqual(obj['error']['code'],'INVALID_REQUEST')

class TutorialStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow=json.loads((ROOT/'workflows'/'tutorial-basics.json').read_text(encoding='utf-8'))

    def test_required_nodes(self):
        kinds={n['type'].rsplit('.',1)[-1] for n in self.workflow['nodes']}
        self.assertTrue({'manualTrigger','set','if','switch','httpRequest','stickyNote'}<=kinds)
        self.assertNotIn('webhook',kinds)
        self.assertNotIn('respondToWebhook',kinds)
        self.assertFalse(self.workflow['active'])

    def test_references_valid_nodes(self):
        names=[n['name'] for n in self.workflow['nodes']]
        self.assertEqual(len(names),len(set(names)))
        for source, outputs in self.workflow['connections'].items():
            self.assertIn(source,names)
            for branch in outputs['main']:
                for con in branch:
                    self.assertIn(con['node'],names)
                    self.assertEqual(con['index'],0)

    def test_http_request_uses_internal_dns(self):
        nodes=[n for n in self.workflow['nodes'] if n['type'].endswith('.httpRequest')]
        self.assertEqual(len(nodes),1)
        self.assertIn('http://mock-api:8080/users/',nodes[0]['parameters']['url'])

if __name__ == '__main__':
    unittest.main()
