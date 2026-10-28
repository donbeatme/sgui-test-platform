import base64
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image
from perception.icons import attach_missing_icons, visual_ids_from_debug


class IconOutputTests(unittest.TestCase):
    def test_raw_yolo_icon_replaces_non_ocr_caption_with_crop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '_debug').mkdir()
            (root / '_debug/screenshot_ocr_elements.json').write_text(json.dumps({
                'elements': [{'element_id': 5, 'source': 'yolo', 'element_type': 'icon'},
                             {'element_id': 6, 'source': 'ocr', 'element_type': 'text'}]}))
            image = root / 'screen.png'
            Image.new('RGB', (8, 8), 'blue').save(image)
            item = {'text': '模型猜测名称', 'type': 'button',
                    'machine': {'id': 5, 'bbox': [1, 1, 5, 5]}}
            result = {'main_workspace': {'elements': [item]}}
            self.assertEqual(visual_ids_from_debug(root), {5})
            self.assertEqual(attach_missing_icons(result, image, visual_ids_from_debug(root)), 1)
            self.assertNotIn('text', item)
            self.assertTrue(item['icon'].startswith('data:image/png;base64,'))

    def test_textless_element_gets_its_actual_crop_without_changing_type_or_text(self):
        with tempfile.TemporaryDirectory() as folder:
            screenshot = Path(folder) / 'screen.png'
            img = Image.new('RGB', (12, 8), 'white')
            for x in range(2, 6):
                for y in range(1, 5):
                    img.putpixel((x, y), (23, 87, 198))
            img.save(screenshot)
            text = {'text': '保存', 'type': 'button', 'machine': {'bbox': [0, 0, 7, 7]}}
            icon = {'type': 'button', 'machine': {'bbox': [2, 1, 6, 5]}}
            result = {'main_workspace': {'elements': [text, icon]}}
            self.assertEqual(attach_missing_icons(result, screenshot), 1)
            self.assertNotIn('icon', text)
            self.assertEqual(icon['type'], 'button')
            self.assertNotIn('text', icon)
            data = icon['icon']
            self.assertTrue(data.startswith('data:image/png;base64,'))
            crop = Image.open(BytesIO(base64.b64decode(data.split(',', 1)[1])))
            self.assertEqual(crop.size, (4, 4))
            self.assertEqual(crop.getpixel((0, 0)), (23, 87, 198))
            self.assertEqual(attach_missing_icons(result, screenshot), 0)


if __name__ == '__main__':
    unittest.main()
