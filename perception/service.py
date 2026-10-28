"""Serve screenshot recognition from the perception package in this checkout.

Example: GUI_PERCEPTION_TOKEN=<random-token> python perception/service.py \
    --host 0.0.0.0 --port 18781
"""
import argparse
import base64
import hmac
import json
import os
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from perception.icons import attach_missing_icons, visual_ids_from_debug


MAX_BYTES = 8 * 1024 * 1024
SUFFIXES = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}


def inline_output_icons(value, output):
    """Make icon crops durable when the perception JSON refers to temp files."""
    if isinstance(value, dict):
        return {key: inline_output_icons(item, output) for key, item in value.items()}
    if isinstance(value, list):
        return [inline_output_icons(item, output) for item in value]
    if isinstance(value, str) and not value.startswith('data:'):
        path = Path(value)
        if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
            absolute = (path if path.is_absolute() else output / path).resolve()
            if absolute.is_relative_to(output.resolve()) and absolute.is_file() and absolute.stat().st_size <= 512 * 1024:
                mime = 'image/jpeg' if absolute.suffix.lower() in ('.jpg', '.jpeg') else 'image/webp' if absolute.suffix.lower() == '.webp' else 'image/png'
                return 'data:' + mime + ';base64,' + base64.b64encode(absolute.read_bytes()).decode('ascii')
    return value


def handler_for(project_root, token, qwen=True):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/health':
                self.reply(200, {'ok': True, 'qwen': qwen})
            else:
                self.reply(404, {'error': 'unknown path'})

        def do_POST(self):
            if self.path != '/recognize':
                return self.reply(404, {'error': 'unknown path'})
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                return self.reply(401, {'error': 'unauthorized'})
            mime = self.headers.get('Content-Type', '').split(';', 1)[0].lower()
            if mime not in SUFFIXES:
                return self.reply(415, {'error': 'unsupported image type'})
            try:
                size = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                size = -1
            if not 0 < size <= MAX_BYTES:
                return self.reply(413, {'error': 'invalid image size'})
            content = self.rfile.read(size)
            if len(content) != size:
                return self.reply(400, {'error': 'incomplete image'})
            try:
                with tempfile.TemporaryDirectory(prefix='gui-perception-') as folder:
                    image = Path(folder) / ('screenshot' + SUFFIXES[mime])
                    image.write_bytes(content)
                    output = Path(folder) / 'result'
                    command = [sys.executable, str(project_root / 'run_ocr.py'), '--input', str(image),
                               '--output-dir', str(output)]
                    if qwen:
                        command.append('--qwen')
                    completed = subprocess.run(
                        command, cwd=project_root, capture_output=True,
                        text=True, timeout=270, check=False)
                    if completed.returncode:
                        return self.reply(502, {'error': 'perception process failed'})
                    result = json.loads((output / 'screenshot_elements.json').read_text(encoding='utf-8'))
                    result = inline_output_icons(result, output)
                    attach_missing_icons(result, image, visual_ids_from_debug(output))
                return self.reply(200, result)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return self.reply(502, {'error': 'perception result unavailable'})

        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-root', type=Path,
                        default=Path(__file__).resolve().parent)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=18781)
    args = parser.parse_args()
    token = os.environ.get('GUI_PERCEPTION_TOKEN', '')
    if len(token) < 32:
        parser.error('GUI_PERCEPTION_TOKEN must contain at least 32 characters')
    root = args.project_root.expanduser().resolve()
    if not (root / 'run_ocr.py').is_file() or not (root / 'OmniParser/weights/icon_detect/model.pt').is_file():
        parser.error('model or entry point missing; run python perception/download_models.py first')
    qwen = os.environ.get('GUI_PERCEPTION_QWEN', '1').lower() not in ('0', 'false', 'no')
    if qwen:
        help_result = subprocess.run([sys.executable, str(root / 'run_ocr.py'), '--help'],
                                     cwd=root, capture_output=True, text=True, timeout=30)
        if help_result.returncode or '--qwen' not in help_result.stdout:
            parser.error('perception/run_ocr.py does not support --qwen; update the migrated runtime or set GUI_PERCEPTION_QWEN=0')
    print(f'Perception service listening on {args.host}:{args.port}; Qwen={qwen}', flush=True)
    HTTPServer((args.host, args.port), handler_for(root, token, qwen=qwen)).serve_forever()


if __name__ == '__main__':
    main()
