import copy

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from projects.models import Project, ProjectMember
from . import grounding as ground
from .models import GenerationJob, CaseExtension
from .services import configuration, normalize_cases, commit_job, canonical_cases
from .tests import fixture


class ConfirmationActionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('batch-reviewer', password='test-only')
        self.project = Project.objects.create(name='Batch confirmation regression', creator=self.user)
        config = configuration(self.project)
        cases = normalize_cases([fixture(tc_sno=f'REVIEW-{i}', tc_name=f'0{i} P1 登录检查 {i}') for i in (1, 2)])
        for case in cases:
            case['grounding_version'] = 1
            case['steps'][0].update(basis_type='inference', evidence_ids=[], uncertainty='材料没有明确失败提示文案')
            case['basis_fingerprint'] = ground.fingerprint(case)
            case['source_review'] = {'verdict': 'needs_confirmation', 'issues': ['需核对预期'], 'fingerprint': ground.fingerprint(case)}
        self.job = GenerationJob.objects.create(project=self.project, creator=self.user, status='draft',
            cases=cases, options={'grounding_version': 1, 'rules': config.rules}, progress={})
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.base = f'/api/projects/{self.project.pk}/workbench/jobs/{self.job.pk}/'

    def payload(self, cases=None, **extra):
        self.job.refresh_from_db()
        cases = cases if cases is not None else canonical_cases(self.job)
        return {'tc_snos': [c['tc_sno'] for c in cases], 'revision': self.job.revision,
                'case_fingerprints': {c['tc_sno']: ground.fingerprint(c) for c in cases}, **extra}

    def post(self, operation, payload):
        return self.api.post(self.base + operation + '/', payload, format='json')

    def test_confirm_all_without_note_and_revoke_preserves_original_evidence_and_audit(self):
        before = copy.deepcopy(self.job.cases)
        response = self.post('confirm-cases', self.payload())
        self.assertEqual(response.status_code, 200, response.content)
        self.job.refresh_from_db()
        batch_ids = {c['human_review']['batch_id'] for c in self.job.cases}
        self.assertEqual(len(batch_ids), 1)
        self.assertNotIn(None, batch_ids)
        for old, current in zip(before, self.job.cases):
            self.assertFalse(ground.confirmation_reasons(current))
            self.assertEqual(current['steps'], old['steps'])
            self.assertEqual(current['source_review'], old['source_review'])
            self.assertEqual(current['human_review']['user_id'], self.user.pk)
            self.assertFalse(current['human_review']['note_provided'])
            self.assertEqual(current['human_review']['accepted_reasons'], ground.confirmation_reasons(old))
        self.assertEqual(self.job.issues['confirmation_required'], [])
        response = self.post('revoke-confirmations', self.payload())
        self.assertEqual(response.status_code, 200, response.content)
        self.job.refresh_from_db()
        for old, current in zip(before, self.job.cases):
            self.assertNotIn('human_review', current)
            self.assertEqual(ground.confirmation_reasons(current), ground.confirmation_reasons(old))
            self.assertEqual(current['human_reviews'][0]['revoked_by'], self.user.pk)
        self.assertEqual(len(self.job.issues['confirmation_required']), 2)

    def test_selection_and_shared_optional_short_note(self):
        response = self.post('confirm-cases', self.payload(self.job.cases[:1], note='已核对'))
        self.assertEqual(response.status_code, 200, response.content)
        self.job.refresh_from_db()
        self.assertEqual(self.job.cases[0]['human_review']['note'], '已核对')
        self.assertNotIn('human_review', self.job.cases[1])
        self.assertEqual(len(self.job.issues['confirmation_required']), 1)

    def test_invalid_target_or_stale_version_or_content_is_atomic(self):
        valid = self.payload()
        for change in ({'tc_snos': [valid['tc_snos'][0], 'foreign-case']},
                       {'tc_snos': [valid['tc_snos'][0]] * 2}, {'tc_snos': []},
                       {'tc_snos': [None]}, {'revision': -1}, {'case_fingerprints': {}},
                       {'note': {'not': 'text'}}, {'note': 'a' * 2001}):
            with self.subTest(change=change.keys()):
                response = self.post('confirm-cases', {**valid, **change})
                self.assertEqual(response.status_code, 400, response.content)
                self.job.refresh_from_db()
                self.assertTrue(all('human_review' not in c for c in self.job.cases))
                self.assertEqual(self.job.revision, valid['revision'])

    def test_edit_invalidates_confirmation_and_old_revision_cannot_confirm(self):
        old_payload = self.payload()
        self.assertEqual(self.post('confirm-cases', old_payload).status_code, 200)
        self.job.refresh_from_db()
        edited = copy.deepcopy(self.job.cases)
        edited[0]['steps'][0]['expected'] = '修改后的预期'
        response = self.post('draft', {'cases': edited, 'revision': self.job.revision})
        self.assertEqual(response.status_code, 200, response.content)
        self.job.refresh_from_db()
        self.assertTrue(ground.confirmation_reasons(self.job.cases[0]))
        self.assertFalse(ground.confirmation_reasons(self.job.cases[1]))
        self.assertEqual(self.post('confirm-cases', old_payload).status_code, 400)

    def test_structural_error_is_not_waived_by_confirmation(self):
        self.job.cases[0]['steps'][0]['expected'] = ''
        self.job.save()
        response = self.post('confirm-cases', self.payload())
        self.assertEqual(response.status_code, 200, response.content)
        self.job.refresh_from_db()
        self.assertEqual(self.job.issues['confirmation_required'], [])
        self.assertGreater(self.job.issues['errors'], 0)
        with self.assertRaises(ValidationError):
            commit_job(self.job.pk, self.project, self.user, self.job.revision)

    def test_saved_cases_support_revoke_and_reconfirm_and_check_live_fingerprint(self):
        self.assertEqual(self.post('confirm-cases', self.payload()).status_code, 200)
        self.job.refresh_from_db()
        commit_job(self.job.pk, self.project, self.user, self.job.revision)
        response = self.post('revoke-confirmations', self.payload())
        self.assertEqual(response.status_code, 200, response.content)
        extension = CaseExtension.objects.filter(job=self.job).first()
        self.assertNotIn('human_review', extension.original)
        self.assertIn('revoked_at', extension.original['human_reviews'][0])
        stale = self.payload()
        step = extension.case.steps.first()
        step.expected_result = '正式用例页面刚修改过的预期'
        step.save()
        self.assertEqual(self.post('confirm-cases', stale).status_code, 400)
        self.assertEqual(self.post('confirm-cases', self.payload()).status_code, 200)
        for case in canonical_cases(self.job):
            self.assertFalse(ground.confirmation_reasons(case))

    def test_revoke_with_unconfirmed_target_does_not_partially_revoke(self):
        self.assertEqual(self.post('confirm-cases', self.payload(self.job.cases[:1])).status_code, 200)
        self.assertEqual(self.post('revoke-confirmations', self.payload()).status_code, 400)
        self.job.refresh_from_db()
        self.assertIn('human_review', self.job.cases[0])

    def test_edit_permission_is_required_and_legacy_single_route_accepts_empty_note(self):
        member = get_user_model().objects.create_user('batch-readonly', password='test-only')
        ProjectMember.objects.create(project=self.project, user=member, role='member')
        member.user_permissions.add(Permission.objects.get(content_type__app_label='testcases', codename='add_testcase'))
        self.api.force_authenticate(member)
        for operation in ('confirm-cases', 'revoke-confirmations'):
            self.assertEqual(self.post(operation, self.payload()).status_code, 403)
        self.api.force_authenticate(self.user)
        response = self.post('confirm-case', {'tc_sno': self.job.cases[0]['tc_sno'], 'revision': self.job.revision})
        self.assertEqual(response.status_code, 200, response.content)

    def test_running_task_cannot_be_confirmed(self):
        self.job.status = 'running'
        self.job.save()
        self.assertEqual(self.post('confirm-cases', self.payload()).status_code, 400)
