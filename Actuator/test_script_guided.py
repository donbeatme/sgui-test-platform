import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from threading import Thread
from test_script_runner import Handler
from script_guided import execute


class GuidedBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_session_keeps_state_and_acknowledges_actual_failure_without_running_later_actions(self):
        requests = []
        async def respond(folder, request):
            requests.append(request)
            if request['seq'] == 0:
                return {'command': {'parameters': {}, 'statements': [
                    'await page.locator("#open").click()',
                    'await page.get_by_role("button", name="Premium", exact=True).click()',
                    'await expect(page.locator("#chosen")).to_have_text("Wrong", timeout=500)',
                    'await page.locator("#toggle").click()']}}
            self.assertEqual([o['ok'] for o in request['outcomes']], [True, True, False])
            self.assertIn('Premium', request['evidence']['aria_snapshot'])
            return {'finished': True}
        with patch('script_guided.exchange', respond):
            result = asyncio.run(execute({'base_url': f'http://127.0.0.1:{self.server.server_port}/custom-fields',
                                         'headless': True}, Path('.')))
        self.assertEqual(result['status'], 'pass')  # Collection only, not delivery or business pass.
        self.assertEqual(len(requests), 2)
        import json
        nodes = json.loads(requests[1]['evidence']['dom'])['nodes']
        self.assertEqual(next(n['text'] for n in nodes if n['attrs'].get('id') == 'counter'), '0')

    def test_http_denial_stops_without_model_planning(self):
        with patch('script_guided.exchange') as planner:
            result = asyncio.run(execute({'base_url': f'http://127.0.0.1:{self.server.server_port}/denied',
                                         'headless': True}, Path('.')))
        self.assertEqual(result['status'], 'blocked')
        planner.assert_not_called()

    def test_variables_survive_batches_and_conditions_match_standalone_execution(self):
        async def respond(folder, request):
            if request['seq'] == 0:
                return {'command': {'parameters': {}, 'statements': ['button = page.locator("#open")',
                    'panel = page.locator("#menu")']}}
            if request['seq'] == 1:
                self.assertTrue(all(o['ok'] for o in request['outcomes']))
                return {'command': {'parameters': {}, 'statements': [
                    'if await button.is_visible():\n    await button.click()',
                    'await page.get_by_role("button", name="Premium", exact=True).click()',
                    'choice = await page.locator("#chosen").inner_text()',
                    'assert choice == "Premium", "Unexpected choice"']}}
            self.assertTrue(all(o['ok'] for o in request['outcomes']), request['outcomes'])
            return {'finished': True}
        with patch('script_guided.exchange', respond):
            result = asyncio.run(execute({'base_url': f'http://127.0.0.1:{self.server.server_port}/custom-fields',
                                         'headless': True}, Path('.')))
        self.assertEqual(result['status'], 'pass')
