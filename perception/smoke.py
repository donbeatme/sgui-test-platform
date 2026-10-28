"""End-to-end HTTP probe for the project's screenshot perception service."""
import argparse
import json
import mimetypes
import os
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def token_from_env():
    token = os.environ.get('GUI_PERCEPTION_TOKEN')
    if token:
        return token
    for line in (ROOT / 'local/.env').read_text(encoding='utf-8').splitlines():
        if line.startswith('GUI_PERCEPTION_TOKEN='):
            return line.partition('=')[2].strip()
    raise RuntimeError('GUI_PERCEPTION_TOKEN is missing from local/.env')


def walk_elements(value):
    if isinstance(value, dict):
        if 'machine' in value and any(key in value for key in ('text', 'icon', 'type')):
            yield value
        else:
            for child in value.values():
                yield from walk_elements(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_elements(child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:18781')
    args = parser.parse_args()
    kind = mimetypes.guess_type(str(args.input))[0]
    if kind not in ('image/png', 'image/jpeg', 'image/webp'):
        parser.error('input must be a PNG, JPEG or WebP screenshot')
    with urlopen(args.url.rstrip('/') + '/health', timeout=5) as response:
        health = json.load(response)
    request = Request(args.url.rstrip('/') + '/recognize', args.input.read_bytes(),
                      headers={'Authorization': 'Bearer ' + token_from_env(), 'Content-Type': kind},
                      method='POST')
    with urlopen(request, timeout=300) as response:
        result = json.load(response)
    elements = list(walk_elements(result))
    if not elements:
        raise RuntimeError('perception service returned no structured elements')
    print('service:', health)
    print('elements:', len(elements))
    print('types:', dict(Counter(str(item.get('type', 'unknown')) for item in elements)))
    print('regions:', [key for key in ('left_global_navigation', 'top_navigation_toolbar',
                                      'tab_area', 'main_workspace', 'bottom_action_bar') if key in result])
    textless = [item for item in elements if not str(item.get('text') or '').strip()]
    icons = sum(str(item.get('icon', '')).startswith('data:image/') for item in textless)
    print('textless elements:', len(textless), 'icon images:', icons)
    if len(textless) != icons:
        raise RuntimeError('some textless elements are missing real icon crops')


if __name__ == '__main__':
    main()
