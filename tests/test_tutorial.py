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

class ExpandedTutorialStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflows = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((ROOT/'workflows').glob('tutorial-*.json'))]

    def test_three_importable_examples(self):
        self.assertEqual(len(self.workflows), 3)
        for flow in self.workflows:
            self.assertFalse(flow['active'])
            names = {n['name'] for n in flow['nodes']}
            self.assertEqual(len(names), len(flow['nodes']))
            for src, outputs in flow['connections'].items():
                self.assertIn(src, names)
                for branch in outputs['main']:
                    for edge in branch:
                        self.assertIn(edge['node'], names)
                        self.assertEqual(edge['index'], 0)

    def test_items_loop_wiring(self):
        flow = next(f for f in self.workflows if 'Items' in f['name'])
        by_name={n['name']:n for n in flow['nodes']}
        self.assertEqual(by_name['Loop: batches of 1']['type'],'n8n-nodes-base.splitInBatches')
        self.assertEqual(by_name['Loop: batches of 1']['parameters']['batchSize'],1)
        outputs=flow['connections']['Loop: batches of 1']['main']
        self.assertEqual(outputs[0][0]['node'],'Done: inspect all')
        self.assertEqual(outputs[1][0]['node'],'Loop: mark item')
        self.assertEqual(flow['connections']['Loop: mark item']['main'][0][0]['node'],'Loop: batches of 1')

    def test_webhook_does_not_conflict_with_assignment(self):
        flow = next(f for f in self.workflows if 'Webhook' in f['name'])
        nodes={n['name']:n for n in flow['nodes']}
        self.assertEqual(nodes['Webhook: tutorial-ping']['parameters']['path'],'tutorial-ping')
        self.assertEqual(nodes['Webhook: tutorial-ping']['parameters']['responseMode'],'responseNode')
        self.assertTrue(any(n['type'].endswith('.respondToWebhook') for n in flow['nodes']))

    def test_references_practical_context(self):
        flow=next(f for f in self.workflows if 'Данные' in f['name'])
        ref=next(n for n in flow['nodes'] if n['name']=='Recall previous node data')
        values=[f['value'] for f in ref['parameters']['assignments']['assignments']]
        self.assertTrue(any("$('Example input').item.json" in str(v) for v in values))

if __name__ == '__main__':
    unittest.main()
