import json
import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from perception.service import handler_for


class PerceptionServerTests(unittest.TestCase):
    def test_service_embeds_missing_icon_crop_from_uploaded_screenshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'run_ocr.py').write_text('''
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--input')
p.add_argument('--output-dir')
p.add_argument('--qwen', action='store_true')
a = p.parse_args()
out = Path(a.output_dir)
out.mkdir(parents=True)
(out / 'screenshot_elements.json').write_text(json.dumps({'main_workspace': {'elements': [
    {'type': 'button', 'machine': {'bbox': [1, 1, 5, 5]}}]}}))
''', encoding='utf-8')
            source = io.BytesIO()
            Image.new('RGB', (8, 8), 'red').save(source, format='PNG')
            payload = source.getvalue()
            handler = handler_for(root, 'test-token', qwen=True)
            instance = handler.__new__(handler)
            instance.path = '/recognize'
            instance.headers = {'Content-Type': 'image/png', 'Authorization': 'Bearer test-token',
                                'Content-Length': str(len(payload))}
            instance.rfile = io.BytesIO(payload)
            replies = []
            instance.reply = lambda status, result: replies.append((status, result))
            instance.do_POST()
            self.assertEqual(replies[0][0], 200)
            icon = replies[0][1]['main_workspace']['elements'][0]['icon']
            self.assertTrue(icon.startswith('data:image/png;base64,'))

    def test_existing_run_ocr_output_is_returned_and_unauthorized_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'run_ocr.py').write_text('''
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--input')
p.add_argument('--output-dir')
p.add_argument('--qwen', action='store_true')
a = p.parse_args()
target = Path(a.output_dir)
target.mkdir(parents=True, exist_ok=True)
(target / 'icons').mkdir()
(target / 'icons/crop.png').write_bytes(b'icon-png')
(target / 'screenshot_elements.json').write_text(json.dumps({'qwen_requested': a.qwen, 'main_workspace': {'elements': [
    {'text': '保存', 'type': 'button', 'machine': {'bbox': [1, 2, 3, 4]}},
    {'icon': 'icons/crop.png', 'type': 'button', 'machine': {'bbox': [5, 6, 7, 8]}}]}}))
''', encoding='utf-8')
            handler = handler_for(root, 'test-token', qwen=True)
            def request(token):
                instance = handler.__new__(handler)
                instance.path = '/recognize'
                instance.headers = {'Content-Type': 'image/png', 'Authorization': 'Bearer ' + token,
                                    'Content-Length': '8'}
                instance.rfile = io.BytesIO(b'png-test')
                replies = []
                instance.reply = lambda status, payload: replies.append((status, payload))
                instance.do_POST()
                return replies[0]
            status, _ = request('wrong')
            self.assertEqual(status, 401)
            status, result = request('test-token')
            self.assertEqual(status, 200)
            self.assertTrue(result['qwen_requested'])
            self.assertEqual(result['main_workspace']['elements'][0]['text'], '保存')
            self.assertTrue(result['main_workspace']['elements'][1]['icon'].startswith('data:image/png;base64,'))
            handler_without_qwen = handler_for(root, 'test-token', qwen=False)
            handler = handler_without_qwen
            status, result = request('test-token')
            self.assertEqual(status, 200)
            self.assertFalse(result['qwen_requested'])


if __name__ == '__main__':
    unittest.main()
