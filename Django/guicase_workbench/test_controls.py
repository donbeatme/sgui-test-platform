import copy
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from . import controls, grounding
from .models import GenerationJob
from .tasks import generate_job
from . import test_grounding as grounding_fixtures
from .views import job_data


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}})
class ControlReviewTests(TestCase):
    setUp = grounding_fixtures.GroundingTests.setUp
    create_job = grounding_fixtures.GroundingTests.create_job

    def fake_model(self, config, prompt, content, **kwargs):
        if controls.SCHEMA in prompt:
            return json.dumps({'images': [{'name': 'login.png', 'page': '登录页面', 'facts': [], 'unknowns': ['点击行为未知'],
                'controls': [{'name': '登录按钮', 'type': 'button', 'text': 'Login', 'state': '可见', 'bbox': [0.2, 0.3, 0.5, 0.1]}]}]})
        return grounding_fixtures.GroundingTests.fake_model(self, config, prompt, content, **kwargs)

    def paused_job(self):
        self.options['control_review_version'] = 1
        job = self.create_job()
        with patch('guicase_workbench.tasks.invoke', side_effect=self.fake_model) as invoked:
            generate_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, 'awaiting_controls', job.notes)
        self.assertEqual(job.cases, [])
        self.assertEqual(invoked.call_count, 1)
        return job

    def payload(self, job, reviewed=True):
        return {'revision': job.revision, 'images': [{'unit_id': p['unit_id'], 'controls': copy.deepcopy(p['controls']), 'reviewed': reviewed}
            for p in job_data(job)['control_images']]}

    def test_pause_correction_and_continue_preserves_original_and_uses_corrected_evidence(self):
        job = self.paused_job()
        payload = self.payload(job)
        item = payload['images'][0]['controls'][0]
        item['name'], item['state'] = '登录提交按钮', '按钮文字清晰可见'
        with patch('guicase_workbench.views.generate_job.delay') as enqueue:
            response = self.api.post(self.base+f'jobs/{job.pk}/continue-controls/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        enqueue.assert_called_once()
        job.refresh_from_db()
        unit = next(u for u in job.progress['units'] if 'controls' in u)
        self.assertEqual(unit['controls'][0]['original']['name'], '登录按钮')
        self.assertEqual(unit['control_review']['user_id'], self.user.pk)
        self.assertEqual(unit['control_history'][0]['after'][0]['name'], '登录提交按钮')
        with patch('guicase_workbench.tasks.invoke', side_effect=self.fake_model) as invoked:
            generate_job(str(job.pk), str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'draft', job.notes)
        self.assertFalse(any(controls.SCHEMA in call.args[1] for call in invoked.call_args_list))
        evidence = job.cases[0]['steps'][0]['evidence_refs'][0]
        self.assertIn('登录提交按钮', evidence['text'])
        self.assertEqual(evidence['control_id'], item['id'])
        self.assertEqual(evidence['bbox'], [0.2, 0.3, 0.5, 0.1])
        self.assertEqual(job_data(job)['control_images'][0]['controls'][0]['case_ids'], [job.cases[0]['tc_sno']])

    def test_save_without_review_and_stale_or_duplicate_continue_cannot_enqueue(self):
        job = self.paused_job()
        payload = self.payload(job, False)
        response = self.api.post(self.base+f'jobs/{job.pk}/controls/', payload, format='json')
        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, 'awaiting_controls')
        with patch('guicase_workbench.views.generate_job.delay') as enqueue:
            for body in (payload, self.payload(job, False)):
                response = self.api.post(self.base+f'jobs/{job.pk}/continue-controls/', body, format='json')
                self.assertEqual(response.status_code, 400)
            enqueue.assert_not_called()
            body = self.payload(job)
            self.assertEqual(self.api.post(self.base+f'jobs/{job.pk}/continue-controls/', body, format='json').status_code, 200)
            self.assertEqual(self.api.post(self.base+f'jobs/{job.pk}/continue-controls/', body, format='json').status_code, 400)
            enqueue.assert_called_once()

    def test_bad_coordinates_and_foreign_ids_roll_back(self):
        job = self.paused_job()
        for change in ({'bbox': [0.9, 0.5, 0.5, 0.1]}, {'id': 'foreign'}, {'bbox': [True, 0, 1, 1]}, {'name': ''}):
            payload = self.payload(job)
            payload['images'][0]['controls'][0].update(change)
            response = self.api.post(self.base+f'jobs/{job.pk}/controls/', payload, format='json')
            self.assertEqual(response.status_code, 400, response.content)
            job.refresh_from_db()
            self.assertEqual(job.revision, 1)
        for bbox in ([float('nan'), 0, 1, 1], [-1, 0, 1, 1], [0, 0, 0, 1]):
            with self.assertRaises(ValidationError):
                controls.normalize_control({'name': 'x', 'type': 'input', 'bbox': bbox})

    def test_exclude_and_add_rebuilds_prompt_without_stale_active_control(self):
        job = self.paused_job()
        payload = self.payload(job)
        item = payload['images'][0]['controls'][0]
        item['excluded'] = True
        payload['images'][0]['controls'].append({'name': '用户名', 'type': 'input', 'text': 'Username', 'state': '空白', 'bbox': None})
        response = self.api.post(self.base+f'jobs/{job.pk}/controls/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        unit = next(u for u in job.progress['units'] if 'controls' in u)
        self.assertNotIn(item['id'], [e['id'] for e in unit['evidence_items']])
        text = json.loads(unit['text'])
        self.assertEqual([c['name'] for c in text['controls']], ['用户名'])
        self.assertEqual(text['excluded_controls'][0]['name'], '登录按钮')
        self.assertEqual(unit['controls'][1]['origin'], 'human')
        self.assertIsNone(unit['controls'][1]['original'])

    def test_read_get_and_permissions_cannot_mutate(self):
        job = self.paused_job()
        payload = self.payload(job)
        with patch('guicase_workbench.views.generate_job.delay') as enqueue:
            self.assertEqual(self.api.get(self.base+f'jobs/{job.pk}/continue-controls/').status_code, 200)
            enqueue.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, 'awaiting_controls')
        other = get_user_model().objects.create_user('no-project-access', password='test')
        self.api.force_authenticate(other)
        response = self.api.post(self.base+f'jobs/{job.pk}/controls/', payload, format='json')
        self.assertIn(response.status_code, (403, 404))

    def test_queue_failure_resumes_after_review_and_legacy_jobs_remain_read_only(self):
        job = self.paused_job()
        with patch('guicase_workbench.views.generate_job.delay', side_effect=RuntimeError('queue down')):
            response = self.api.post(self.base+f'jobs/{job.pk}/continue-controls/', self.payload(job), format='json')
        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        with patch('guicase_workbench.views.generate_job.delay'):
            self.assertEqual(self.api.post(self.base+f'jobs/{job.pk}/resume/', {}, format='json').status_code, 200)
        job.refresh_from_db()
        with patch('guicase_workbench.tasks.invoke', side_effect=self.fake_model):
            generate_job(str(job.pk), str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'draft', job.notes)
        response = self.api.post(self.base+f'jobs/{job.pk}/controls/', self.payload(job), format='json')
        self.assertEqual(response.status_code, 400)

    def test_missing_control_array_fails_and_text_only_skips_gate(self):
        with self.assertRaises(ValidationError):
            controls.validate_vision({'images': [{'name': 'x.png', 'facts': []}]}, ['x.png'])
        self.options['control_review_version'] = 1
        job = self.create_job(False)
        with patch('guicase_workbench.tasks.invoke', side_effect=self.fake_model):
            generate_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.status, 'draft', job.notes)

    def test_no_unreviewed_image_can_be_omitted_and_control_deletion_requires_exclusion(self):
        job = self.paused_job()
        payload = self.payload(job)
        payload['images'] = []
        self.assertEqual(self.api.post(self.base+f'jobs/{job.pk}/continue-controls/', payload, format='json').status_code, 400)
        payload = self.payload(job)
        payload['images'][0]['controls'] = []
        self.assertEqual(self.api.post(self.base+f'jobs/{job.pk}/controls/', payload, format='json').status_code, 400)
