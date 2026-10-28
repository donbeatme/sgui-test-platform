import json
from unittest.mock import patch
from django.test import TestCase
from . import tests as fixtures
from .consolidation import consolidate


class ConsolidationTests(TestCase):
    setUp = fixtures.ReplayTests.setUp

    def test_consolidation_is_only_a_candidate_until_replay(self):
        original = fixtures.CODE.replace('        await expect', '        form = page.locator("body")\n        await expect')
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps({'code': fixtures.CODE})), \
                patch('langgraph_integration.models.LLMConfig.objects.get'):
            code, warnings, metadata = consolidate(self.script, original, {'base_url': 'http://localhost'})
        self.assertTrue(metadata['ok'])
        self.assertEqual(code, fixtures.CODE)
        self.assertNotEqual(metadata['input_sha256'], metadata['output_sha256'])
        self.assertFalse(self.version.runs.filter(status='pass').exists())

    def test_missing_steps_or_model_failure_keeps_source_candidate_and_warning(self):
        with patch('guicase_workbench.tasks.invoke', return_value=json.dumps({'code': 'async def run(page, params, record):\n    return'})), \
                patch('langgraph_integration.models.LLMConfig.objects.get'):
            code, warnings, metadata = consolidate(self.script, fixtures.CODE, {'base_url': 'http://localhost'})
        self.assertFalse(metadata['ok'])
        self.assertEqual(code, fixtures.CODE)
        self.assertTrue(warnings)
