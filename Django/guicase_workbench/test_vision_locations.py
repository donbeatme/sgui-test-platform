from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from . import controls


class VisionLocationTests(SimpleTestCase):
    def payload(self, box):
        return {'images': [{'name': 'test.jpg', 'page': '搜索', 'facts': [], 'unknowns': [],
                            'controls': [{'name': '搜索', 'type': 'button', 'text': '搜索',
                                          'state': '可见', 'bbox': box}]}]}

    def test_invalid_model_box_keeps_control_without_fabricating_location(self):
        for box in ([0.9, 0.5, 0.5, 0.1], [100, 200, 50, 20], [True, 0, 1, 1]):
            payload = self.payload(box)
            c = controls.validate_vision(payload, ['test.jpg'])['test.jpg']['controls'][0]
            self.assertEqual(c['text'], '搜索')
            self.assertIsNone(c['bbox'])
            self.assertIn('无效', c['bbox_warning'])
            self.assertEqual(c['model_bbox'], repr(box))
            self.assertEqual(payload['images'][0]['controls'][0]['bbox'], box)
            with self.assertRaises(ValidationError):
                controls.normalize_control(payload['images'][0]['controls'][0])

    def test_valid_locations_and_other_validation_remain_strict(self):
        box = [0.2, 0.3, 0.4, 0.1]
        c = controls.validate_vision(self.payload(box), ['test.jpg'])['test.jpg']['controls'][0]
        self.assertEqual(c['bbox'], box)
        self.assertNotIn('bbox_warning', c)
        payload = self.payload([20, 30, 40, 10])
        payload['images'][0]['controls'][0]['name'] = ''
        with self.assertRaises(ValidationError):
            controls.validate_vision(payload, ['test.jpg'])
