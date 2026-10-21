"""Lifecycle tests: real-build results still pass through the normal evidence judge."""
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from . import tests as fixtures
from .models import ScriptBuild, ScriptRun, ScriptWorker
from .services import save_version
from .contract import validate, runtime_required
from .builds import (options_for, start_build, claim_observation, observation_update,
                     generated, run_finished, cancel_build, expire_builds)


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}})
class BuildTests(TestCase):
    setUp = fixtures.ReplayTests.setUp
    payload = fixtures.ReplayTests.payload
    complete = fixtures.ReplayTests.complete

    def start(self):
        self.script.generation_mode = 'case'
        self.script.generation_context = {'base_url':'http://localhost:8765'}
        self.script.save()
        self.worker.runtime_version = 9; self.worker.save()
        options = options_for({'validate_live':True, 'worker_id':self.worker.worker_id}, self.user)
        options['strategy'] = 'legacy'  # Existing builds keep their original protocol.
        return start_build(self.script, self.user, options)

    def observed(self):
        build = self.start()
        job = claim_observation(self.user, self.worker.worker_id)
        with patch('playwright_scripts.builds.enqueue_generation') as enqueue, self.captureOnCommitCallbacks(execute=True):
            observation_update(self.user, self.worker.worker_id, 'complete', {
                'run_id':build.pk, 'lease_token':job['lease_token'], 'status':'pass',
                'observation':{'url':'http://localhost:8765', 'dom':'actual DOM'}})
        enqueue.assert_called_once()
        build.refresh_from_db()
        return build

    def validating(self):
        build = self.observed()
        version = save_version(self.script.pk, fixtures.CODE, {'base_url':'http://localhost:8765'})
        generated(self.script.pk, build.generation_token, version)
        build.refresh_from_db()
        return build

    def claim(self):
        return self.payload(self.client.post('/api/playwright-worker/claim/', {
            'worker_id':self.worker.worker_id, 'runtime_version':6, 'supports_headed':True}, format='json'))['job']

    def test_live_is_opt_in_and_requires_own_new_online_worker(self):
        self.assertIsNone(options_for({}, self.user))
        with self.assertRaises(ValidationError):
            options_for({'validate_live':True, 'worker_id':self.worker.worker_id}, self.user)
        self.worker.runtime_version=9; self.worker.save()
        self.assertEqual(options_for({'validate_live':True,'worker_id':self.worker.worker_id},self.user)['timeout_seconds'],180)
        self.worker.heartbeat_at=timezone.now()-timedelta(minutes=1);self.worker.save()
        with self.assertRaises(ValidationError):
            options_for({'validate_live':True,'worker_id':self.worker.worker_id},self.user)

    def test_claim_once_cancel_fences_observation_and_generation(self):
        build=self.start();job=claim_observation(self.user,self.worker.worker_id)
        self.assertEqual(job['kind'],'observe')
        self.assertIsNone(claim_observation(self.user,self.worker.worker_id))
        cancel_build(self.script,self.user)
        with patch('playwright_scripts.builds.enqueue_generation') as enqueue:
            observation_update(self.user,self.worker.worker_id,'complete', {
                'run_id':build.pk,'lease_token':job['lease_token'],'status':'pass','observation':{}})
        enqueue.assert_not_called()
        generated(self.script.pk, uuid.uuid4(), self.version)
        build.refresh_from_db();self.assertEqual(build.status,'cancelled')
        self.assertFalse(ScriptRun.objects.exists())

    def test_denied_entry_preserves_observation_without_model_call(self):
        build=self.start();job=claim_observation(self.user,self.worker.worker_id)
        with patch('playwright_scripts.builds.enqueue_generation') as enqueue:
            observation_update(self.user,self.worker.worker_id,'complete',{
                'run_id':build.pk,'lease_token':job['lease_token'],'status':'blocked',
                'summary':'入口返回 HTTP 403','observation':{'http_status':403,'dom':'Access denied'}})
        enqueue.assert_not_called()
        build.refresh_from_db()
        self.assertEqual(build.status,'blocked')
        self.assertEqual(build.observation['http_status'],403)
        self.assertFalse(ScriptRun.objects.exists())

    def test_observed_build_validates_with_frozen_case_and_normal_evidence(self):
        build=self.validating()
        self.assertEqual(build.attempt,1)
        self.assertEqual(build.status,'validating')
        self.assertEqual(build.last_run.result.case_snapshot,self.script.snapshot)
        job=self.claim()
        with self.captureOnCommitCallbacks(execute=True):
            self.payload(self.complete(job))
        build.refresh_from_db()
        self.assertEqual(build.status,'ready')
        self.assertEqual(build.last_run.result.execution.passed_count,1)
        self.assertEqual(build.last_run.result.evidence.count(),1)

    def test_failures_are_retained_and_repair_is_bounded(self):
        build=self.validating()
        for attempt in range(1,4):
            job=self.claim()
            with patch('playwright_scripts.builds.enqueue_generation'),self.captureOnCommitCallbacks(execute=True):
                self.payload(self.complete(job,'fail'))
            build.refresh_from_db()
            self.assertEqual(ScriptRun.objects.filter(result__status='fail').count(),attempt)
            if attempt<3:
                self.assertEqual(build.attempt,attempt+1)
                version=save_version(self.script.pk,fixtures.CODE,{'base_url':'http://localhost:8765'})
                generated(self.script.pk,build.generation_token,version)
                build.refresh_from_db()
            else:
                self.assertEqual(build.status,'failed')
                self.assertEqual(build.attempt,3)

    def test_skip_does_not_mark_verified_and_has_no_repair(self):
        build=self.validating();job=self.claim()
        with self.captureOnCommitCallbacks(execute=True):
            self.payload(self.complete(job,'skip',{'condition':{'matched':['normal'],'outcome':'skip','reason':'exception not triggered'}}))
        build.refresh_from_db()
        self.assertEqual(build.status,'not_applicable')
        self.assertEqual(build.last_run.result.execution.passed_count,0)

    def test_semantic_draft_never_auto_executes(self):
        build=self.observed()
        version=save_version(self.script.pk,fixtures.CODE,{'base_url':'http://localhost:8765'},extra_issues=['缺少业务断言'])
        generated(self.script.pk,build.generation_token,version)
        build.refresh_from_db()
        self.assertEqual(build.status,'draft')
        self.assertIsNone(build.last_run_id)

    def test_reviewed_diagnostic_never_passes_and_only_runs_once_per_build(self):
        build=self.observed()
        version=save_version(self.script.pk,fixtures.CODE,{'base_url':'http://localhost:8765'},
                             extra_issues=['缺少操作后校验颜色的现场依据'])
        review={'safe_actions':True,'missing_evidence_only':True,'reason':'动作均属原用例，需采集字段样式'}
        generated(self.script.pk,build.generation_token,version,diagnostic_review=review)
        build.refresh_from_db()
        job=self.payload(self.client.post('/api/playwright-worker/claim/',{
            'worker_id':self.worker.worker_id,'runtime_version':6,'supports_headed':True},format='json'))['job']
        self.assertTrue(job['diagnostic_only'])
        with patch('playwright_scripts.builds.enqueue_generation'),self.captureOnCommitCallbacks(execute=True):
            self.payload(self.complete(job,'pass'))
        build.refresh_from_db()
        self.assertEqual(build.last_run.result.status,'blocked')
        self.assertEqual(build.last_run.result.execution.passed_count,0)
        self.assertEqual(build.attempt,2)
        next_version=save_version(self.script.pk,fixtures.CODE,{'base_url':'http://localhost:8765'},
                                 extra_issues=['仍缺少依据'])
        generated(self.script.pk,build.generation_token,next_version,diagnostic_review=review)
        build.refresh_from_db()
        self.assertEqual(build.status,'draft')
        self.assertEqual(ScriptRun.objects.count(),1)

    def test_diagnostic_cannot_bypass_invalid_code(self):
        build=self.observed()
        version=save_version(self.script.pk,fixtures.CODE+'        await page.evaluate("fetch(1)")\n',
            {'base_url':'http://localhost:8765'})
        generated(self.script.pk,build.generation_token,version,diagnostic_review={
            'safe_actions':True,'missing_evidence_only':True,'reason':'untrusted approval'})
        build.refresh_from_db()
        self.assertEqual(build.status,'draft')
        self.assertIsNone(build.last_run_id)

    def test_same_day_verified_code_is_reused_but_must_replay(self):
        build=self.validating()
        version=build.last_run.version
        version.origin='ai_case';version.save()
        job=self.claim()
        with self.captureOnCommitCallbacks(execute=True):
            self.payload(self.complete(job))
        next_build=self.start();probe=claim_observation(self.user,self.worker.worker_id)
        with patch('playwright_scripts.builds.enqueue_generation') as enqueue,self.captureOnCommitCallbacks(execute=True):
            observation_update(self.user,self.worker.worker_id,'complete',{
                'run_id':next_build.pk,'lease_token':probe['lease_token'],'status':'pass',
                'observation':{'dom':'fresh DOM'}})
        enqueue.assert_not_called()
        next_build.refresh_from_db()
        self.assertEqual(next_build.status,'validating')
        self.assertEqual(next_build.last_run.version.code,version.code)
        self.assertEqual(next_build.last_run.version.number,version.number+1)
        self.assertEqual(next_build.last_run.result.status,'pending')

    def test_cancel_during_validation_prevents_late_pass_or_retry(self):
        build=self.validating();job=self.claim()
        cancel_build(self.script,self.user)
        with patch('playwright_scripts.builds.enqueue_generation') as enqueue,self.captureOnCommitCallbacks(execute=True):
            self.payload(self.complete(job))
        enqueue.assert_not_called()
        build.refresh_from_db()
        self.assertEqual(build.status,'cancelled')
        self.assertEqual(build.last_run.result.status,'cancelled')

    def test_live_worker_queue_does_not_expire_because_it_is_busy(self):
        build=self.start()
        ScriptBuild.objects.filter(pk=build.pk).update(created_at=timezone.now()-timedelta(minutes=5))
        expire_builds();build.refresh_from_db()
        self.assertEqual(build.status,'pending_observation')
        ScriptWorker.objects.filter(pk=self.worker.pk).update(heartbeat_at=timezone.now()-timedelta(minutes=2))
        expire_builds();build.refresh_from_db()
        self.assertEqual(build.status,'error')

    def test_active_build_disables_manual_run(self):
        self.validating()
        response=self.client.post(self.base+f'{self.script.pk}/run/', {
            'version':self.version.pk,'worker_id':self.worker.worker_id},format='json')
        self.assertEqual(response.status_code,400)

    def test_native_negative_assertions_and_checked_helper_contract(self):
        code=fixtures.CODE+'''        await record.set_toggle(control=page.locator("#toggle"), selected=page.locator("#toggle[data-state=on]"), unselected=page.locator("#toggle[data-state=off]"), checked=False)
        await expect(page.locator("#toggle")).not_to_have_attribute("data-state","on")
'''
        self.assertEqual(validate(code,self.script.snapshot,{'base_url':'http://localhost:8765'}),[])
        self.assertEqual(runtime_required(code),5)
        self.assertTrue(validate(code.replace('checked=False','checked="no"'),self.script.snapshot,{'base_url':'http://localhost:8765'}))
        malicious=code.replace('control=page.locator("#toggle")','control=page.locator(page.goto("http://other.example"))')
        self.assertTrue(validate(malicious,self.script.snapshot,{'base_url':'http://localhost:8765'}))
