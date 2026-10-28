"""Attach actual screenshot crops to textless visual elements.

The legacy observer emits a control type and bbox for visual-only detections,
but some versions omit the crop from the formal partitioned JSON. This stage
adds pixels only; it does not infer labels, types or interaction state.
"""
import base64
import json
import math
from io import BytesIO
from pathlib import Path

from PIL import Image


def elements_in(value):
    if isinstance(value, dict):
        if isinstance(value.get('machine'), dict) and 'type' in value:
            yield value
        else:
            for child in value.values():
                yield from elements_in(child)
    elif isinstance(value, list):
        for child in value:
            yield from elements_in(child)


def visual_ids_from_debug(output):
    """Use the observer's own evidence to recognize icon-only detections."""
    output = Path(output)
    candidates = list(output.glob('_debug/**/*_ocr_elements.json')) + list(output.glob('*_ocr_elements.json'))
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and isinstance(raw.get('elements'), list):
            return {item['element_id'] for item in raw['elements']
                    if item.get('source') == 'yolo' and item.get('element_type') == 'icon'
                    and isinstance(item.get('element_id'), int)}
    return set()


def attach_missing_icons(result, screenshot, visual_ids=()):
    """Embed a PNG crop for every textless element with an actual pixel bbox."""
    screenshot = Path(screenshot)
    visual_ids = set(visual_ids)
    pending = [item for item in elements_in(result)
               if (item.get('machine', {}).get('id') in visual_ids
                   or not str(item.get('text') or '').strip()
                   or str(item.get('text')).strip().casefold() == 'unknown_icon')
               and not item.get('icon')]
    if not pending:
        return 0
    count = 0
    with Image.open(screenshot) as raw:
        image = raw.convert('RGB')
        width, height = image.size
        for item in pending:
            bbox = item['machine'].get('bbox')
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                coords = [float(n) for n in bbox]
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(n) for n in coords):
                continue
            left, top = max(0, math.floor(coords[0])), max(0, math.floor(coords[1]))
            right, bottom = min(width, math.ceil(coords[2])), min(height, math.ceil(coords[3]))
            if right <= left or bottom <= top:
                continue
            buffer = BytesIO()
            image.crop((left, top, right, bottom)).save(buffer, format='PNG')
            item['icon'] = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
            if item['machine'].get('id') in visual_ids or str(item.get('text')).strip().casefold() == 'unknown_icon':
                item.pop('text', None)
            count += 1
    return count


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Add actual icon crops to an existing perception JSON')
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--json', type=Path, required=True)
    parser.add_argument('--debug-dir', type=Path, help='run_ocr.py output directory containing _debug')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.json.read_text(encoding='utf-8'))
    count = attach_missing_icons(data, args.image,
                                 visual_ids_from_debug(args.debug_dir) if args.debug_dir else ())
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'icon crops added: {count}; output: {args.output}')
