import json
from copy import deepcopy
from datetime import date
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from . import tests as fixtures
from .guided import initialize, restart_learning, claim, update, plan, fragment_statements
from .builds import start_build
from .planning import validate_plan, obligations, cached_steps, patch_statements, contradictory_absence, reject_frozen_relative_dates
from .date_parameters import resolve_parameters
from .contract import validate, runtime_required
from .export import standalone
from .quality import review_notes, required_warning_gaps


class PlanningRulesTests(SimpleTestCase):
    def test_review_note_objects_preserve_findings_and_required_gaps_are_not_optional(self):
        notes = review_notes([{'step': 3, 'message': '缺少断言'}, 'Other finding'])
        self.assertIn('缺少断言', notes[0])
        self.assertEqual(review_notes(None), [])
        with self.assertRaises(ValueError): review_notes([False])
        case = {'steps': [{'step_number': 3, 'expected_result': '正确回填且无校验错误提示'}]}
        self.assertEqual(required_warning_gaps(['未单独断言“无校验错误提示”，以字段值间接覆盖，建议补充。'], case)[0]['step_number'], 3)
        self.assertEqual(required_warning_gaps(['未实现未要求的登录流程。'], case), [])
    def test_missing_precondition_and_expected_check_cannot_disappear(self):
        case = {'precondition': 'Existing error must be triggered first', 'steps': [{'step_number': 1, 'expected_result': 'Recovered'}]}
        good = {'setup': [{'source_id': 'P1', 'step_number': 1, 'prepare': 'Trigger error', 'verify': 'Error visible'}],
                'steps': [{'step_number': 1, 'action': 'Recover', 'checks': [{'source_id': 'E1', 'verify': 'Value restored'}]}]}
        self.assertEqual(obligations(case)[0]['source'], case['precondition'])
        validate_plan(good, case)
        for key in ('setup', 'steps'):
            bad = deepcopy(good); bad[key] = []
            with self.assertRaises(ValueError): validate_plan(bad, case)

    def test_absence_claim_uses_current_visible_value_not_historical_summary(self):
        page = {'dom': json.dumps({'nodes': [
            {'visible': True, 'css': '#tier', 'value': '不限舱等'},
            {'visible': False, 'css': '#old', 'text': 'Economy'}]})}
        self.assertEqual(contradictory_absence('不限舱等', page)[0]['css'], '#tier')
        self.assertFalse(contradictory_absence('Economy', page))
        self.assertFalse(contradictory_absence('missing', {'dom': 'incomplete'}))

    def test_local_patch_preserves_preparation_and_later_assertion(self):
        old = ['await page.locator("#city").fill("")', 'await page.locator("#search").click()',
               'await expect(page.locator("#error")).to_be_visible()']
        patched = patch_statements(old, [{'index': 1, 'delete_count': 0,
            'body': 'await record.dismiss_panel(panel=page.locator("#panel"), dismiss=page.locator("#close"))'}],
            fragment_statements, 1, {})
        self.assertEqual(patched[0], old[0]); self.assertEqual(patched[2:], old[1:])
        with self.assertRaises(ValueError):
            patch_statements(old, [{'index': 2, 'delete_count': 1, 'body': ''}], fragment_statements, 1, {})

    def test_cache_does_not_reuse_affected_or_unfinished_steps(self):
        snapshot = {'steps': [{'step_number': n} for n in (1, 2, 3)]}
        previous = {'step_index': 2, 'accepted': {'1': ['a'], '2': ['b'], '3': ['partial']}}
        cache, candidates = cached_steps(previous, snapshot, {'step_numbers': [2]})
        self.assertEqual(cache, {'1': ['a']})
        self.assertEqual(candidates, {'2': ['b'], '3': ['partial']})
        self.assertEqual(cached_steps(previous, snapshot, {})[0], {})
        previous['recovery_tail'] = ['failed assertion', 'not yet executed assertion']
        _, candidates = cached_steps(previous, snapshot, {'step_numbers': [3]})
        self.assertEqual(candidates['3'], ['partial', 'failed assertion', 'not yet executed assertion'])

    def test_relative_dates_recompute_across_month_year_and_keep_fixed_dates(self):
        params = {'d': '{{date:7:iso}}', 'day': '{{date:7:day}}', 'weekday': '{{date:7:weekday_zh_short}}', 'fixed': '2026-12-31'}
        result = resolve_parameters(params, date(2026, 12, 28))
        self.assertEqual(result, {'d': '2027-01-04', 'day': '4', 'weekday': '周一', 'fixed': '2026-12-31'})
        self.assertEqual(params['d'], '{{date:7:iso}}')
        self.assertEqual(runtime_required(fixtures.CODE, params), 8)
        with self.assertRaises(ValueError): resolve_parameters({'d': '{{date:7:bad}}'})
        with self.assertRaises(ValueError):
            reject_frozen_relative_dates(['await expect(page.locator("#date")).to_have_value("2027-01-04")'], params, date(2026,12,28))
        reject_frozen_relative_dates(['await expect(page.locator("#date")).to_have_value(params["d"])'], params, date(2026,12,28))


class ScopedRepairTests(TestCase):
    setUp = fixtures.ReplayTests.setUp

    def start(self):
        self.script.generation_context = {'base_url': 'http://localhost:8765'}
        self.script.snapshot = {**self.script.snapshot, 'precondition': 'Prepare the initial state',
            'steps': [{'step_number': n, 'description': f'Action {n}', 'expected_result': f'Check {n}'} for n in (1, 2, 3)]}
        self.script.save()
        self.build = start_build(self.script, self.user, {'worker_id': self.worker.worker_id,
            'headless': True, 'timeout_seconds': 180, 'strategy': 'guided-v2'})
        self.job = claim(self.build)
        self.identity = {'run_id': self.build.pk, 'lease_token': self.job['lease_token']}

    def next(self, seq, outcomes=None, evidence=None):
        with patch('playwright_scripts.guided.plan.delay'), patch('playwright_scripts.guided.finalize.delay'):
            return update(self.user, self.worker.worker_id, 'next', {**self.identity, 'seq': seq,
                'outcomes': outcomes, 'evidence': evidence or {}})

    def test_repair_replays_prior_step_without_model_and_rechecks_every_statement(self):
        self.start()
        self.build.observation['guided'].update(step_index=3, accepted={
            '1': ['await expect(page.locator("#ready")).to_be_visible()'],
            '2': ['await page.locator("#go").click()', 'await expect(page.locator("#ok")).to_be_visible()'],
            '3': ['await expect(page.locator("#done")).to_be_visible()']})
        self.build.save()
        restart_learning(self.build, {'error': 'overlay', 'page': {'step_number': 2}})
        self.job = claim(self.build)
        self.identity['lease_token'] = self.job['lease_token']
        first = self.next(0)['command']
        self.assertTrue(first['cached']); self.assertEqual(first['step_number'], 1)
        self.next(1, [{'ok': False, 'error': 'initial state changed'}])
        self.build.refresh_from_db()
        state = self.build.observation['guided']
        self.assertEqual(state['step_index'], 0)
        self.assertEqual(state['recovery_tail'], first['statements'])
        self.assertNotIn('await expect(page.locator("#ready")).to_be_visible()', state['accepted']['1'])
        self.assertEqual(state['repair_candidates']['2'][0], 'await page.locator("#go").click()')

    def test_preparation_is_saved_before_actions_and_reused_on_repair(self):
        self.start(); self.next(0)
        # This specifically exercises the retained strict policy, not replay_once.
        self.build.refresh_from_db()
        self.build.observation.pop('acceptance', None)
        self.build.save()
        prepared = {'setup': [{'source_id': 'P1', 'step_number': 1, 'prepare': 'Prepare', 'verify': 'Verify'}],
            'steps': [{'step_number': n, 'action': f'Action {n}', 'checks': [{'source_id': f'E{n}', 'verify': f'Check {n}'}]} for n in (1, 2, 3)]}
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps(prepared)), patch('langgraph_integration.models.LLMConfig.objects.get'):
            plan(self.build.pk, self.job['lease_token'], 0)
        self.build.refresh_from_db()
        self.assertEqual(self.build.observation['execution_plan']['setup'], prepared['setup'])
        self.assertEqual(self.build.observation['execution_plan']['steps'][0]['source_expected'], 'Check 1')
        saved = deepcopy(self.build.observation['execution_plan'])
        self.assertIsNone(self.build.observation['guided']['command'])
        restart_learning(self.build, {'error': 'Failed', 'step_numbers': [1]})
        claim(self.build)
        self.assertEqual(self.build.observation['execution_plan'], saved)

    def test_visible_target_rejects_false_missing_control_claim(self):
        self.start()
        self.build.observation['execution_plan'] = {'setup': [], 'steps': []}
        self.build.save()
        self.next(0, evidence={'dom': json.dumps({'nodes': [{'visible': True, 'css': '#tier', 'text': 'Any class'}]})})
        response = {'blocked': 'Any class does not exist', 'blocker': {'kind': 'missing_control', 'target_text': 'Any class', 'evidence': 'Missing'}}
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps(response)), patch('langgraph_integration.models.LLMConfig.objects.get'):
            plan(self.build.pk, self.job['lease_token'], 0)
        self.build.refresh_from_db()
        state = self.build.observation['guided']
        self.assertIn('矛盾', state['format_feedback']['error'])
        self.assertFalse(state['error'])
        self.assertIsNone(state['command'])

    def test_exploratory_patch_stays_on_source_step_and_does_not_replay_consumed_candidate(self):
        self.start()
        self.build.observation['execution_plan'] = {'setup': [], 'steps': []}
        self.build.observation['guided'].update(step_index=1, repair_candidates={
            '2': ['await page.locator("#picker").click()',
                  'await expect(page.locator("#value")).to_have_value("D")']})
        self.build.save()
        self.next(0)
        partial = {'patches': [{'index': 0, 'delete_count': 1,
            'body': 'await record.ensure_open(trigger=page.locator("#picker"), panel=page.locator("#panel"))'}],
            'parameters': {}, 'complete': False, 'blocked': ''}
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps(partial)), patch('langgraph_integration.models.LLMConfig.objects.get'):
            plan(self.build.pk, self.job['lease_token'], 0)
        self.build.refresh_from_db()
        command = self.build.observation['guided']['command']
        self.assertFalse(command['complete'])
        self.next(1, [{'ok': True} for _ in command['statements']])
        self.build.refresh_from_db()
        state = self.build.observation['guided']
        self.assertEqual(state['step_index'], 1)
        self.assertNotIn('2', state['repair_candidates'])
        self.assertEqual(state['accepted']['2'], command['statements'])
        extra = {'body': 'await expect(page.locator("#selected-D")).to_be_visible()',
                 'parameters': {}, 'complete': True, 'blocked': ''}
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps(extra)), patch('langgraph_integration.models.LLMConfig.objects.get'):
            plan(self.build.pk, self.job['lease_token'], 1)
        self.build.refresh_from_db()
        final = self.build.observation['guided']['command']
        self.assertEqual(final['step_number'], 2)
        self.next(2, [{'ok': True} for _ in final['statements']])
        self.build.refresh_from_db()
        state = self.build.observation['guided']
        self.assertEqual(state['step_index'], 2)
        self.assertEqual(len(state['accepted']['2']), 3)
        self.assertIn('#selected-D', state['accepted']['2'][-1])
