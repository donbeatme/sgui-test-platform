import base64
import io
import json
from unittest.mock import patch
from PIL import Image
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from projects.models import Project
from testcases.models import TestCase as Case, TestCaseModule, TestCaseStep, TestCaseResult, TestExecution
from testcases.execution_tracking import begin_execution, finish_execution
from .models import SavedScript, ScriptVersion, ScriptWorker, ScriptRun
from .services import enable_capture, record_event, save_version, finalize_capture
from .contract import validate, normalize, runtime_required
from .tasks import generate_script
from .export import standalone

CODE = '''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await expect(page.get_by_role("heading", name="Replay fixture", exact=True)).to_be_visible()
'''


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}})
class ReplayTests(TestCase):
    def setUp(self):
        review = patch('playwright_scripts.tasks.semantic_review', return_value=([], []))
        self.quality_review = review.start()
        self.addCleanup(review.stop)
        self.user = User.objects.create_superuser(username='replay-admin', password='test-only')
        self.project = Project.objects.create(name='Replay fixture', creator=self.user)
        module = TestCaseModule.objects.create(project=self.project, name='Replay')
        self.case = Case.objects.create(project=self.project, module=module, name='Open fixture')
        TestCaseStep.objects.create(test_case=self.case, step_number=1, description='Open fixture', expected_result='Heading Replay fixture is visible')
        self.source = begin_execution(self.project, self.user, self.case.pk, 'replay-source')
        enable_capture(self.source)
        self.script = SavedScript.objects.get(source_result=self.source)
        self.version = save_version(self.script.pk, CODE, {'base_url': 'http://127.0.0.1:8765'})
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.base = f'/api/projects/{self.project.pk}/playwright-scripts/'
        self.worker = ScriptWorker.objects.create(worker_id='test-worker', user=self.user, heartbeat_at=timezone.now())

    def payload(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content[:1000])
        value = response.json()
        return value.get('data', value)

    def run_script(self):
        return self.payload(self.client.post(self.base+f'{self.script.pk}/run/', {'version': self.version.pk, 'worker_id': self.worker.worker_id}, format='json'), 201)

    def test_download_archive_uses_non_reserved_format_parameter(self):
        import zipfile
        response = self.client.get(self.base+f'{self.script.pk}/download/',
            {'version': self.version.pk, 'download_format': 'zip'})
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn('case.py', archive.namelist())
        response = self.client.get(self.base+f'{self.script.pk}/download/',
            {'version': self.version.pk, 'download_format': 'py'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('async def run', response.content.decode())

    def claim(self):
        return self.payload(self.client.post('/api/playwright-worker/claim/', {'worker_id': self.worker.worker_id, 'supports_headed': True}, format='json'))['job']

    def complete(self, job, status='pass', step_extra=None):
        stream=io.BytesIO(); Image.new('RGB',(10,10),'white').save(stream,format='PNG')
        item = {'step_number':1,'status':status,'actual':'Heading observed','screenshot':base64.b64encode(stream.getvalue()).decode()}
        if step_extra:
            item.update(step_extra)
        return self.client.post('/api/playwright-worker/complete/', {'worker_id': self.worker.worker_id,
            'run_id':job['id'],'lease_token':job['lease_token'],'status':status,'summary':'Fixture checked',
            'steps':[item]}, format='json')

    def test_complete_records_evidence_statistics_and_version(self):
        run=self.run_script(); job=self.claim()
        self.assertEqual(job['code'], CODE)
        self.payload(self.complete(job))
        result=TestCaseResult.objects.get(execution_id=run['execution_id'])
        self.assertEqual(result.status,'pass')
        self.assertEqual(result.evidence.count(),1)
        self.assertEqual(result.execution.source,'playwright_script')
        self.assertEqual(result.execution.passed_count,1)
        detail=self.payload(self.client.get(self.base+f'{self.script.pk}/'))
        self.assertTrue(detail['versions'][0]['verified'])
        self.payload(self.complete(job))
        self.assertEqual(result.evidence.count(),1) # callback idempotency

    def test_conditional_skip_has_evidence_and_separate_statistics(self):
        run = self.run_script(); job = self.claim()
        response = self.complete(job, 'skip', {'actual':'实际正常工作区，异常场景未触发',
            'condition':{'matched':['正常工作区'], 'outcome':'skip', 'reason':'异常场景未触发'}})
        self.payload(response)
        result = TestCaseResult.objects.get(execution_id=run['execution_id'])
        self.assertEqual(result.status, 'skip')
        execution = result.execution
        self.assertEqual((execution.status, execution.skipped_count, execution.passed_count,
                          execution.failed_count, execution.blocked_count), ('skipped', 1, 0, 0, 0))
        self.assertEqual(result.step_results[0]['condition']['matched'], ['正常工作区'])
        self.assertFalse(self.payload(self.client.get(self.base+f'{self.script.pk}/'))['latest']['verified'])
        self.assertEqual(ScriptRun.objects.get(pk=run['id']).status, 'skip')

    def test_skip_cannot_be_used_without_condition_or_screenshot(self):
        for extra in [None, {'condition':{'matched':[], 'outcome':'skip', 'reason':'not observed'}},
                      {'screenshot':'', 'condition':{'matched':['normal'], 'outcome':'skip', 'reason':'condition unmet'}}]:
            run = self.run_script(); job = self.claim()
            self.payload(self.complete(job, 'skip', extra))
            result = TestCaseResult.objects.get(execution_id=run['execution_id'])
            self.assertEqual(result.status, 'blocked')
            self.assertEqual(result.execution.skipped_count, 0)

    def test_remaining_steps_after_observed_condition_skip_are_not_run(self):
        snapshot = dict(self.script.snapshot)
        snapshot['steps'] = [*snapshot['steps'], {'step_number':2,'description':'conditional action','expected_result':'conditional expected'}]
        self.script.snapshot = snapshot; self.script.save()
        self.version = save_version(self.script.pk, CODE + '''    async with record.step(2):
        await expect(page.locator("h1")).to_be_visible()
''', {'base_url':'http://localhost'})
        run = self.run_script(); job = self.claim()
        self.payload(self.complete(job, 'skip', {'condition':{'matched':['normal'], 'outcome':'skip', 'reason':'exception not triggered'}}))
        result = TestCaseResult.objects.get(execution_id=run['execution_id'])
        self.assertEqual(result.status, 'skip')
        self.assertEqual(result.step_results[1]['status'], 'not_run')
        self.assertIn('未触发', result.step_results[1]['reason'])

    def test_cancel_fences_late_success(self):
        self.run_script();job=self.claim()
        self.payload(self.client.post(self.base+f'{self.script.pk}/stop/',{'run_id':job['id']},format='json'))
        self.payload(self.complete(job))
        self.assertEqual(ScriptRun.objects.get(pk=job['id']).result.status,'cancelled')
        self.assertFalse(self.version.runs.filter(status='pass').exists())

    def test_script_versions_immutable_and_conflicts_detected(self):
        v=save_version(self.script.pk,CODE,{'base_url':'http://localhost'},base_version=1)
        self.assertEqual(v.number,2)
        self.version.refresh_from_db();self.assertEqual(self.version.parameters['base_url'],'http://127.0.0.1:8765')
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError): save_version(self.script.pk,CODE,{},base_version=1)

    def test_unchecked_runs_do_not_create_scripts(self):
        run=begin_execution(self.project,self.user,self.case.pk,'without-save')
        finish_execution(run.pk,forced='blocked',reason='Fixture')
        with patch('playwright_scripts.tasks.generate_script.delay') as task:
            finalize_capture(run.pk)
            task.assert_not_called()
        self.assertEqual(SavedScript.objects.count(),1)

    def test_generation_is_separate_and_based_on_recorded_events(self):
        SavedScript.objects.filter(pk=self.script.pk).update(generation_status='recording')
        record_event(self.source.pk,'call-1','browser_navigate',{'url':'http://localhost'},'### Ran Playwright code\nawait page.goto("http://localhost")')
        self.source.status='pass';self.source.save()
        with patch('playwright_scripts.tasks.generate_script.delay') as task:
            finalize_capture(self.source.pk);finalize_capture(self.source.pk)
            task.assert_called_once()
        self.script.refresh_from_db()
        from langgraph_integration.models import LLMConfig
        model=LLMConfig.objects.create(config_name='fixture',name='fixture',api_url='http://localhost/v1',is_active=True)
        with patch('guicase_workbench.tasks.invoke',return_value=json.dumps({'code':CODE,'parameters':{'base_url':'http://localhost'},'limitations':[]})) as invoke:
            generate_script(self.script.pk,str(self.script.generation_token))
            self.assertIn('browser_navigate',invoke.call_args.args[2])
        self.assertEqual(self.script.versions.count(),2)
        self.assertEqual(TestExecution.objects.count(),1) # saving never executes

    def test_unsafe_or_incomplete_code_cannot_run(self):
        for bad in ['import os\n'+CODE,CODE.replace('page.goto(params["base_url"])','page.evaluate("1")'),CODE.replace('await expect(page.get_by_role("heading", name="Replay fixture", exact=True)).to_be_visible()','await page.click("button")')]:
            self.assertTrue(validate(bad,self.script.snapshot))
        self.assertFalse(validate(CODE,self.script.snapshot))
        self.version.issues=['Missing locator'];self.version.save()
        self.assertEqual(self.client.post(self.base+f'{self.script.pk}/run/',{'version':self.version.pk,'worker_id':'test-worker'},format='json').status_code,400)

    def test_permissions_cross_project_and_worker_auth(self):
        other=Project.objects.create(name='Other',creator=self.user)
        self.assertEqual(self.client.get(f'/api/projects/{other.pk}/playwright-scripts/{self.script.pk}/').status_code,404)
        outsider=User.objects.create_user('outsider',password='test-only')
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.base).status_code,403)
        self.assertEqual(self.client.post('/api/playwright-worker/claim/',{'worker_id':'intruder'},format='json').status_code,403)

    def test_download_is_standalone_python(self):
        code=standalone(self.version)
        compile(code,'case.py','exec')
        self.assertIn('async_playwright()',code)
        self.assertIn('class Recorder(PopupActions)',code)
        self.assertIn('async def choose_option',code)

    def helper_code(self):
        return CODE.replace('        await expect', '''        await record.choose_option(trigger=page.locator("#city"), panel=page.locator("#cities"),
            value=page.locator("#city"), expected_value=params["city"], option_name="北京", option_role="listitem")
        await record.expect_closed(panel=page.locator("#cities"))
        await expect''')

    def test_popup_helpers_are_validated_without_allowing_arbitrary_operations(self):
        code = self.helper_code()
        self.assertEqual(validate(code, self.script.snapshot), [])
        self.assertEqual(runtime_required(code), 1)
        self.assertEqual(runtime_required(CODE), 0)
        for bad in [code.replace('trigger=page.locator("#city")', 'trigger=page.click("#city")'),
                    code.replace('option_name="北京"', 'option_name="北京", option_selector="li"'),
                    code.replace('option_name="北京"', 'option_name="北京", force=True'),
                    code.replace('option_name="北京"', 'option_name=page.click("button")'),
                    code.replace('option_name="北京"', 'option_name="北京", timeout_ms=60000'),
                    code.replace('record.choose_option', 'record.step.choose_option'),
                    code.replace('await expect(page.get_by_role("heading", name="Replay fixture", exact=True)).to_be_visible()',
                                 'await record.ensure_open(trigger=page.locator("#city"), panel=page.locator("#cities"))')]:
            self.assertTrue(validate(bad, self.script.snapshot), bad)

    def test_helper_scripts_require_capable_worker_at_start_and_claim(self):
        self.version = save_version(self.script.pk, self.helper_code(), {'base_url':'http://localhost', 'city':'北京'})
        response = self.client.post(self.base+f'{self.script.pk}/run/',
            {'version':self.version.pk, 'worker_id':self.worker.worker_id}, format='json')
        self.assertEqual(response.status_code, 400)
        self.worker.runtime_version = 1; self.worker.save()
        self.run_script()
        # A worker downgraded between scheduling and claim must not execute unsupported code.
        self.assertIsNone(self.claim())
        run = ScriptRun.objects.get(version=self.version)
        self.assertEqual(run.result.status, 'error')

    def test_capable_worker_can_claim_helper_script(self):
        self.version = save_version(self.script.pk, self.helper_code(), {'base_url':'http://localhost', 'city':'北京'})
        self.worker.runtime_version = 1; self.worker.save()
        self.run_script()
        data = self.payload(self.client.post('/api/playwright-worker/claim/',
            {'worker_id':self.worker.worker_id,'supports_headed':True,'runtime_version':1}, format='json'))
        self.assertEqual(data['job']['code'], self.version.code)

    def test_recovery_evidence_and_actual_record_are_preserved(self):
        self.run_script(); job = self.claim()
        stream = io.BytesIO(); Image.new('RGB', (10,10), 'white').save(stream, format='PNG')
        picture = base64.b64encode(stream.getvalue()).decode()
        response = self.complete(job, step_extra={'recovery_screenshots':[picture], 'actual':'首次浮层关闭，恢复一次后断言通过。'})
        self.assertEqual(self.payload(response)['status'], 'pass')
        result = ScriptRun.objects.get(pk=job['id']).result
        self.assertEqual(result.evidence.count(), 2)
        self.assertTrue(result.evidence.filter(title__contains='恢复前失败现场').exists())
        self.assertIn('首次浮层关闭', result.step_results[0]['actual'])

    def test_recovery_picture_does_not_substitute_final_screenshot(self):
        self.run_script(); job = self.claim()
        stream = io.BytesIO(); Image.new('RGB', (10,10), 'white').save(stream, format='PNG')
        response = self.complete(job, step_extra={'screenshot':'', 'recovery_screenshots':[base64.b64encode(stream.getvalue()).decode()]})
        self.assertEqual(self.payload(response)['status'], 'blocked')

    def test_delete_retains_history(self):
        self.run_script();job=self.claim();self.payload(self.complete(job))
        self.payload(self.client.delete(self.base+f'{self.script.pk}/'))
        self.assertTrue(SavedScript.objects.get(pk=self.script.pk).deleted)
        self.assertEqual(self.version.runs.count(),1)

    def test_stale_worker_blocks_start(self):
        self.worker.delete()
        self.assertEqual(self.client.post(self.base+f'{self.script.pk}/run/',{'version':self.version.pk,'worker_id':'test-worker'},format='json').status_code,400)

    def test_missing_screenshot_never_passes(self):
        self.run_script();job=self.claim()
        response=self.client.post('/api/playwright-worker/complete/',{'worker_id':'test-worker','run_id':job['id'],
            'lease_token':job['lease_token'],'status':'pass','steps':[{'step_number':1,'status':'pass','actual':'claimed pass'}]},format='json')
        self.assertEqual(self.payload(response)['status'],'blocked')

    def test_wrong_lease_cannot_complete(self):
        self.run_script();job=self.claim();job['lease_token']='wrong-token'
        self.assertEqual(self.complete(job).status_code,403)

    def test_assertion_failure_is_not_reported_as_pass(self):
        self.run_script();job=self.claim()
        self.assertEqual(self.payload(self.complete(job,'fail'))['status'],'fail')

    def test_stale_generation_cannot_overwrite_manual_edit(self):
        token=str(self.script.generation_token)
        save_version(self.script.pk,CODE,{},base_version=1)
        self.assertIsNone(save_version(self.script.pk,CODE,{},origin='ai',token=token))

    def test_parameter_interpolation_and_css_assertions(self):
        code = '''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await page.locator(f"[data-date='{params['date']}']").click()
        await expect(page.locator(f"#{params.get('panel', 'city')}")).to_have_css("opacity", "0")
'''
        self.assertFalse(validate(code, self.script.snapshot))
        for expression in ["page.click('button')", "params.get('date', page.click('button'))",
                           "params['date']!r", "params['date']:>100000000", "params['date'].__class__"]:
            bad = code.replace("params['date']", expression)
            self.assertTrue(validate(bad, self.script.snapshot), expression)

    def test_warnings_do_not_block_but_errors_still_do(self):
        self.version = save_version(self.script.pk, CODE, {'base_url': 'http://localhost'},
            warnings=['日期来自原执行，请在运行前更新。'])
        detail = self.payload(self.client.get(self.base + f'{self.script.pk}/'))
        self.assertEqual(detail['status'], 'ready')
        self.assertEqual(detail['latest']['issues'], [])
        self.assertEqual(detail['latest']['warnings'], ['日期来自原执行，请在运行前更新。'])
        self.run_script()
        self.version.issues = ['步骤 1 缺少关键断言。']; self.version.save()
        response = self.client.post(self.base + f'{self.script.pk}/run/',
            {'version': self.version.pk, 'worker_id': self.worker.worker_id}, format='json')
        self.assertEqual(response.status_code, 400)

    def generation(self, outputs):
        from langgraph_integration.models import LLMConfig
        LLMConfig.objects.create(config_name='fixture', name='fixture', api_url='http://localhost/v1', is_active=True)
        self.source.status = 'pass'; self.source.save()
        SavedScript.objects.filter(pk=self.script.pk).update(generation_status='queued', events=[
            {'tool':'browser_navigate', 'arguments':{'url':'http://localhost'}, 'output':'recorded', 'truncated':True}])
        self.script.refresh_from_db()
        with patch('guicase_workbench.tasks.invoke', side_effect=[json.dumps(o) if isinstance(o, dict) else o for o in outputs]) as invoke:
            generate_script(self.script.pk, str(self.script.generation_token))
        return invoke, self.script.versions.first()

    def test_generation_repairs_format_once_and_keeps_warnings(self):
        bad = {'code': CODE.replace('async def run', 'def run'), 'blockers': [], 'warnings': []}
        good = {'code':CODE, 'parameters':{'base_url':'http://localhost'}, 'blockers':[], 'warnings':['固定日期需核对。']}
        invoke, version = self.generation([bad, good])
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(version.issues, [])
        self.assertIn('固定日期需核对。', version.warnings)
        self.assertTrue(any('截断' in w for w in version.warnings))
        self.assertEqual(self.script.versions.count(), 2)
        self.assertEqual(TestExecution.objects.count(), 1)

    def test_malformed_model_json_gets_one_repair_and_full_validation(self):
        good = {'code': CODE, 'parameters': {'base_url':'http://localhost'}, 'blockers':[], 'warnings':[]}
        invoke, version = self.generation(['{"code":"broken " JSON"}', good])
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(version.code, CODE)
        self.assertEqual(version.issues, [])
        self.quality_review.assert_called_once()
        repaired_input = json.loads(invoke.call_args.args[2])
        self.assertEqual(repaired_input['generation_input']['case'], self.script.snapshot)
        self.assertTrue(any('JSON' in w and '修复' in w for w in version.warnings))

    def test_repeated_invalid_json_stops_without_overwriting_existing_version(self):
        invoke, version = self.generation(['{"code":', '{"code":'])
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(version.pk, self.version.pk)
        self.script.refresh_from_db()
        self.assertEqual(self.script.generation_status, 'error')
        self.assertIn('JSON 格式修复未成功', self.script.generation_error)
        self.quality_review.assert_not_called()

    def test_json_repair_cannot_bypass_business_review(self):
        good = {'code': CODE, 'parameters': {'base_url':'http://localhost'}, 'blockers':[], 'warnings':[]}
        self.quality_review.return_value = (['原业务操作遗漏。'], [])
        invoke, version = self.generation(['invalid JSON', good, good])
        self.assertEqual(invoke.call_count, 3)
        self.assertIn('原业务操作遗漏。', version.issues)
        self.script.refresh_from_db()
        self.assertEqual(self.script.generation_status, 'draft')

    def test_regeneration_uses_previous_code_and_failure_evidence(self):
        self.run_script(); job = self.claim(); self.payload(self.complete(job, 'fail'))
        invoke, version = self.generation([{'code':CODE, 'parameters':{'base_url':'http://localhost'}, 'blockers':[], 'warnings':[]}])
        evidence = json.loads(invoke.call_args.args[2])
        self.assertEqual(evidence['previous_version']['code'], CODE)
        self.assertEqual(evidence['replay_failure']['version'], 1)
        self.assertEqual(evidence['replay_failure']['steps'][0]['actual'], 'Heading observed')
        self.assertEqual(version.issues, [])

    def test_repair_failure_keeps_first_draft(self):
        bad = {'code':CODE.replace('async def run', 'def run'), 'parameters':{}, 'blockers':[], 'warnings':[]}
        invoke, version = self.generation([bad, RuntimeError('fixture model unavailable')])
        self.assertEqual(invoke.call_count, 2)
        self.assertTrue(version.issues)
        self.assertEqual(version.code, bad['code'])
        self.assertTrue(any('自动格式修复未完成' in w for w in version.warnings))

    def test_semantic_blocker_survives_valid_format(self):
        response = {'code':CODE, 'parameters':{},
            'blockers':['步骤 1 缺少可靠定位器，未覆盖关键预期。'], 'warnings':['使用替代图标断言。']}
        invoke, version = self.generation([response, response])
        self.assertEqual(invoke.call_count, 2)
        self.assertIn('步骤 1 缺少可靠定位器，未覆盖关键预期。', version.issues)
        self.script.refresh_from_db(); self.assertEqual(self.script.generation_status, 'draft')

    def test_manual_revision_preserves_context_warnings(self):
        self.version = save_version(self.script.pk, CODE, {}, warnings=['固定日期。'])
        data = self.payload(self.client.post(self.base+f'{self.script.pk}/versions/',
            {'code':CODE, 'parameters':{}, 'base_version':self.version.number}, format='json'), 201)
        self.assertEqual(data['warnings'], ['固定日期。'])

    def test_legacy_limitations_remain_blocking_until_reviewed(self):
        from .tasks import review_messages
        blockers, warnings = review_messages({'limitations':['缺失日期步骤'], 'warnings':['提示']})
        self.assertEqual(blockers, ['缺失日期步骤'])
        self.assertEqual(warnings, ['提示'])

    def test_omitted_assertions_cannot_be_labeled_as_warnings(self):
        from .tasks import review_messages
        missing = ['无校验错误提示无法用稳定定位器直接断言。',
                   '未对候选弹层已收起做可见性断言。', '城市面板类弹层未覆盖。',
                   '候选面板已收起未做可见性断言，仅检查输入值。']
        harmless = ['无法使用 to_be_checked，改为断言图标 class。',
                    '第4步未再次点击目的地，脚本已先断言候选可见。', '固定日期需更新。']
        blockers, notes = review_messages({'warnings': missing + harmless})
        self.assertEqual(blockers, missing)
        self.assertEqual(notes, harmless)
        # Existing stored versions must also obey the consistency check.
        self.version.warnings = missing; self.version.save()
        data = self.payload(self.client.get(self.base + f'{self.script.pk}/'))
        self.assertEqual(data['status'], 'draft')
        self.assertEqual(data['latest']['issues'], missing)
        response = self.client.post(self.base + f'{self.script.pk}/run/',
            {'version':self.version.pk, 'worker_id':self.worker.worker_id}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_model_misclassified_omission_triggers_repair(self):
        bad = {'code':CODE, 'parameters':{}, 'warnings':['未对错误提示做断言。']}
        good = {'code':CODE, 'parameters':{'base_url':'http://localhost'}, 'blockers':[], 'warnings':[]}
        invoke, version = self.generation([bad, good])
        self.assertEqual(invoke.call_count, 2)
        self.assertIn('未对错误提示做断言', invoke.call_args.args[2])
        self.assertEqual(version.issues, [])

    def test_javascript_locator_methods_normalized_to_python_properties(self):
        code = CODE.replace('page.get_by_role("heading", name="Replay fixture", exact=True)',
                            'page.locator("h1").first()')
        self.assertTrue(validate(code, self.script.snapshot))
        normalized, notes = normalize(code)
        self.assertNotIn('.first()', normalized)
        self.assertIn('.first)', normalized)
        self.assertFalse(validate(normalized, self.script.snapshot))
        version = save_version(self.script.pk, code, {'base_url':'http://localhost'})
        self.assertEqual(version.code, normalized)
        self.assertEqual(version.issues, [])
        self.assertEqual(version.warnings, notes)
        bad = code.replace('.first()', '.first(1)')
        self.assertEqual(normalize(bad)[0], bad)
        self.assertTrue(validate(bad, self.script.snapshot))

    def test_runtime_parameter_override_is_checked_before_creating_run(self):
        code = CODE + '        await expect(page).to_have_url(params["expected_url"])\n'
        version = save_version(self.script.pk, code, {'base_url':'http://localhost', 'expected_url':'http://localhost'})
        response = self.client.post(self.base+f'{self.script.pk}/run/',
            {'version':version.pk, 'worker_id':self.worker.worker_id,
             'parameters':{'expected_url':'**localhost**'}}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ScriptRun.objects.exists())

    def test_failure_page_diagnostics_are_saved_redacted_and_used_for_regeneration(self):
        self.run_script(); job = self.claim()
        stream = io.BytesIO(); Image.new('RGB', (10,10), 'white').save(stream, format='PNG')
        response = self.client.post('/api/playwright-worker/complete/', {
            'worker_id':self.worker.worker_id, 'run_id':job['id'], 'lease_token':job['lease_token'],
            'status':'fail', 'summary':'Failed assertion',
            'steps':[{'step_number':1, 'status':'fail', 'actual':'wrong title',
                      'screenshot':base64.b64encode(stream.getvalue()).decode()}],
            'diagnostics':{'step_number':1, 'url':'http://localhost', 'title':'Observed',
                           'aria_snapshot':'Heading fixture; token: private-token', 'extra':'not stored'}
        }, format='json')
        self.payload(response)
        invoke, _ = self.generation([{'code':CODE, 'parameters':{'base_url':'http://localhost'}, 'blockers':[], 'warnings':[]}])
        diagnostics = json.loads(invoke.call_args.args[2])['replay_failure']['page_diagnostics']
        self.assertIn('Observed', diagnostics['title'])
        self.assertNotIn('private-token', str(diagnostics))
        self.assertNotIn('extra', diagnostics)
