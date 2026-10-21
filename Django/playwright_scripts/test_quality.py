import json
from unittest.mock import patch
from django.test import SimpleTestCase
from .contract import validate, runtime_required
from .quality import semantic_review

SNAPSHOT = {'steps': [{'step_number': 1, 'expected_result': '正常表单可见'}]}
CODE = '''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await expect(page).to_have_url(params["url"])
'''


class QualityTests(SimpleTestCase):
    def test_glob_parameter_is_not_an_exact_url_assertion(self):
        for url in ['**ctrip.com**', '^https://ctrip.com.*$', 'https://example.com/*']:
            self.assertTrue(any('完整 URL' in x for x in validate(CODE, SNAPSHOT, {'base_url': 'https://example.com', 'url': url})))
        self.assertEqual(validate(CODE, SNAPSHOT, {'base_url': 'https://example.com', 'url': 'https://example.com/?q=a'}), [])
        self.assertTrue(any('缺少必填参数' in x for x in validate(CODE, SNAPSHOT, {'base_url': 'https://example.com'})))

    def test_observation_stop_contract_and_version(self):
        code = '''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"], wait_until="domcontentloaded")
        await record.observe_state(states={"正常表单": page.locator("#searchForm")})
        await record.stop(reason="记录后停止，不判定为业务通过")
'''
        snapshot = {'steps': [{'step_number': 1, 'expected_result': '记录后停止，不判定为业务通过'}]}
        self.assertEqual(validate(code, snapshot, {'base_url': 'https://example.com'}), [])
        self.assertEqual(runtime_required(code), 2)
        self.assertTrue(validate(code.replace('page.locator("#searchForm")', 'page.click("#searchForm")'), snapshot))
        # Business keywords must not force an unconditional stop. Semantic review
        # evaluates the condition in the context of the complete case.
        self.assertEqual(validate(code.replace('        await record.stop(reason="记录后停止，不判定为业务通过")', ''), snapshot), [])
        self.assertTrue(validate(code + '        await expect(page.locator("body")).to_be_visible()\n', snapshot))

    def test_no_forced_click_escape(self):
        code = CODE.replace('await expect(page)', 'await page.get_by_text("北京").click(force=True)\n        await expect(page)')
        self.assertTrue(any('force' in x for x in validate(code, SNAPSHOT, {'base_url': 'https://example.com', 'url': 'https://example.com'})))

    def review(self, response):
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps(response)):
            return semantic_review(None, {'case': SNAPSHOT}, CODE, {})

    def test_semantic_review_requires_all_steps_and_explicit_verdict(self):
        for response in ({}, {'steps': []}, {'steps': [{'step_number':1,'covered':True}]}):
            self.assertTrue(self.review(response)[0])
        self.assertEqual(self.review({'steps':[{'step_number':1,'covered':True,'reason':'checks form'}], 'blockers':[], 'warnings':[]}), ([], []))
        issues, _ = self.review({'steps':[{'step_number':1,'covered':False,'reason':'URL替代了表单断言'}], 'blockers':[], 'warnings':[]})
        self.assertIn('URL替代', issues[0])

    def test_provider_failure_does_not_mark_review_passed(self):
        with patch('guicase_workbench.tasks.invoke', side_effect=RuntimeError('private-provider-key')):
            issues, _ = semantic_review(None, {'case':SNAPSHOT}, CODE, {})
        self.assertTrue(issues)
        self.assertNotIn('private-provider-key', str(issues))

    def test_payment_failure_is_actionable_without_provider_payload(self):
        from .errors import model_error_message
        class ProviderError(Exception):
            status_code = 402
        error = ProviderError('private-provider-key and response body')
        self.assertIn('余额或额度不足', model_error_message(error))
        with patch('guicase_workbench.tasks.invoke', side_effect=error):
            issues, _ = semantic_review(None, {'case': SNAPSHOT}, CODE, {})
        self.assertIn('402', str(issues))
        self.assertNotIn('private-provider-key', str(issues))

    def test_site_unavailable_is_not_a_claim_that_assertions_are_unimplemented(self):
        from .review import separate_notes
        warning = '入口无法访问提供定位器的依据不足；实际异常会作为未知状态记录并停止。'
        self.assertEqual(separate_notes([], [warning]), ([], [warning]))
        missing = '无法用稳定定位器断言查询结果。'
        self.assertEqual(separate_notes([], [missing]), ([missing], []))

    def test_ui_boundary_does_not_implicitly_require_network_counting(self):
        from .review import separate_notes
        warning='未点击搜索的停止边界通过最终 URL、表单可见和必填值完整保留，未做网络请求层计数验证。'
        case={'steps':[{'expected_result':'表单可见，未发起查询'}]}
        self.assertEqual(separate_notes([], [warning],case),([], [warning]))
        required={'steps':[{'expected_result':'抓包确认搜索请求次数为 0'}]}
        self.assertEqual(separate_notes([], [warning],required),([warning], []))
        mixed=warning+'；缺少日期选中状态断言。'
        self.assertEqual(separate_notes([], [mixed],case),([mixed], []))
        # Explicit model blockers and immutable old issues are never demoted.
        self.assertEqual(separate_notes([warning],[],case),([warning],[]))

    def test_review_does_not_confuse_old_parameters_with_corrected_candidate(self):
        response = {'steps':[{'step_number':1,'covered':True,'reason':'matches candidate'}], 'blockers':[], 'warnings':[]}
        evidence = {'case':SNAPSHOT, 'previous_version':{'parameters':{'date':'2026-10-22'}},
                    'context':{'generation_date':'2026-10-20', 'reference_version':{'parameters':{'date':'2026-10-22'}}}}
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps(response)) as invoke:
            self.assertEqual(semantic_review(None, evidence, CODE, {'date':'2026-10-27'}), ([], []))
        content = json.loads(invoke.call_args.args[2])
        self.assertEqual(content['candidate']['parameters']['date'], '2026-10-27')
        self.assertIn('blocked', content['runtime_contract']['browser_errors'])
        self.assertIn('not_run', content['runtime_contract']['steps'])
        self.assertNotIn('2026-10-22', invoke.call_args.args[2])

    def test_generic_state_guard_contract(self):
        code = '''async def run(page, params, record):
    async with record.step(1):
        await page.goto(params["base_url"])
        await record.guard_state(states={"工作区":page.get_by_role("main"),"阻断":page.get_by_role("dialog")},
            outcomes={"工作区":"continue","阻断":"blocked"})
        await expect(page.get_by_role("main")).to_be_visible()
'''
        self.assertEqual(validate(code, SNAPSHOT, {'base_url':'http://fixture.test'}), [])
        self.assertEqual(runtime_required(code), 3)
        for invalid in [code.replace('"continue"', '"pass"'), code.replace('"阻断":"blocked"', '"其他":"blocked"'),
                        code.replace('page.get_by_role("main"),', 'page.click("main"),')]:
            self.assertTrue(validate(invalid, SNAPSHOT))

    def test_prompts_respect_conditional_context_without_site_specific_rules(self):
        from .tasks import PROMPT
        from .quality import REVIEW_PROMPT
        self.assertIn('结合用例名称、前置条件、步骤描述和预期', PROMPT)
        self.assertIn('guard_state', PROMPT)
        self.assertNotIn('必须在最后原步骤末尾 await record.stop', PROMPT)
        self.assertNotIn('ctrip.com', PROMPT)
        self.assertIn('把条件操作写成无条件停止', REVIEW_PROMPT)
