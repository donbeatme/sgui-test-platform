"""Perception is a review gate; the latest case generation path stays intact."""
import io
import json
import os
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from . import controls
from .tasks import generate_job
from . import test_controls as control_fixtures
from .views import job_data


def result():
    return {'image': {'width': 16, 'height': 16},
            'main_workspace': {'elements': [
                {'type': 'button', 'text': 'Login',
                 'machine': {'id': 1, 'bbox': [3, 5, 11, 7]}}]}}


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}})
class PerceptionReviewTests(TestCase):
    setUp = control_fixtures.ControlReviewTests.setUp
    create_job = control_fixtures.ControlReviewTests.create_job
    payload = control_fixtures.ControlReviewTests.payload
    fake_model = control_fixtures.ControlReviewTests.fake_model

    def local_job(self):
        self.options.update(control_review_version=1, image_analysis='perception_v1')
        return self.create_job()

    def run_perception(self, job):
        with patch('guicase_workbench.perception_client.recognize', return_value=result()) as recognize, \
                patch('guicase_workbench.tasks.invoke') as model:
            generate_job(str(job.pk), str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'awaiting_controls', job.notes)
        self.assertEqual(job.cases, [])
        recognize.assert_called_once()
        model.assert_not_called()
        return job

    def test_recognition_pauses_before_generation_and_keeps_raw_result_separate(self):
        job = self.run_perception(self.local_job())
        data = job_data(job)
        item = data['control_images'][0]['controls'][0]
        self.assertEqual(item['origin'], 'perception')
        self.assertEqual(item['bbox'], [0.1875, 0.3125, 0.5, 0.125])
        self.assertEqual(item['region'], 'main_workspace')
        self.assertEqual(data['image_analysis'], 'perception_v1')
        path = job.progress['perception_artifacts']['P001']
        with default_storage.open(path) as source:
            self.assertEqual(json.load(source), result())

    def test_corrected_controls_continue_through_existing_case_generation(self):
        job = self.run_perception(self.local_job())
        body = self.payload(job)
        body['images'][0]['controls'][0].update(name='核对后的登录按钮', text='Sign in')
        with patch('guicase_workbench.views.generate_job.delay') as enqueue:
            response = self.api.post(self.base + f'jobs/{job.pk}/continue-controls/', body, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        enqueue.assert_called_once()
        job.refresh_from_db()
        with patch('guicase_workbench.perception_client.recognize') as recognize, \
                patch('guicase_workbench.tasks.invoke', side_effect=self.fake_model):
            generate_job(str(job.pk), str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'draft', job.notes)
        recognize.assert_not_called()
        self.assertIn('核对后的登录按钮', job.cases[0]['steps'][0]['evidence_refs'][0]['text'])

    def test_failed_second_image_resumes_without_repeating_first_image(self):
        job = self.local_job()
        job.input['images'].append({**job.input['images'][0], 'name': 'second.png'})
        job.save()
        with patch('guicase_workbench.perception_client.recognize', side_effect=[result(), ConnectionError('private')]):
            generate_job(str(job.pk), str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertIn('P001', job.progress['vision_outputs'])
        self.assertNotIn('private', json.dumps(job.notes))
        with patch('guicase_workbench.views.generate_job.delay'):
            response = self.api.post(self.base + f'jobs/{job.pk}/resume/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.run_perception(job)
        self.assertEqual(set(job.progress['vision_outputs']), {'P001', 'P002'})

    def test_api_accepts_perception_with_only_text_model_configured(self):
        self.vision.delete()
        source = io.BytesIO()
        Image.new('RGB', (16, 16), 'white').save(source, 'PNG')
        with patch.dict(os.environ, {'GUI_PERCEPTION_URL': 'http://example.invalid',
                                     'GUI_PERCEPTION_TOKEN': 'a' * 64}), \
                patch('guicase_workbench.views.generate_job.delay'):
            response = self.api.post(self.base + 'generate/', {
                'image_analysis': 'perception_v1', 'images': SimpleUploadedFile('local.png', source.getvalue(), 'image/png')},
                format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data['image_analysis'], 'perception_v1')

    def test_text_only_and_invalid_mode_do_not_change_original_behavior(self):
        with patch('guicase_workbench.views.generate_job.delay'):
            response = self.api.post(self.base + 'generate/', {
                'image_analysis': 'perception_v1', 'text': '登录场景'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        response = self.api.post(self.base + 'generate/', {
            'image_analysis': 'unknown', 'text': '登录场景'}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_perception_geometry_and_textless_icons_are_reviewable(self):
        payload = result()
        payload['main_workspace']['elements'][0].update(text='', icon='data:image/png;base64,aA==')
        page = controls.page_from_perception(payload, 'login.png')
        self.assertEqual(page['controls'][0]['type'], 'icon')
        self.assertTrue(page['controls'][0]['name'])
        self.assertEqual(page['controls'][0]['text'], '')
        self.assertEqual(controls.normalize_control({
            'name': '原有控件', 'text': '', 'type': 'button', 'state': '', 'bbox': None}),
            {'name': '原有控件', 'text': '', 'type': 'button', 'state': '', 'bbox': None, 'excluded': False})
