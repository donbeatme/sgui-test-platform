import json
import unittest
from unittest.mock import Mock, patch
from WHartTest_tools import get_the_list_of_use_cases


class CaseListToolTest(unittest.TestCase):
    def test_legacy_and_paginated_responses(self):
        cases = [{"id": 270, "name": "城市互换"}]
        for payload in [cases, {"data": cases}, {"results": cases, "count": 1}, {"data": {"count": 1, "results": cases}}]:
            with self.subTest(payload=payload), patch("WHartTest_tools.requests.get") as get:
                get.return_value = Mock(json=lambda: payload)
                result = json.loads(get_the_list_of_use_cases(13, 52))
                self.assertEqual(result, [{"case_id": 270, "case_name": "城市互换"}])
                get.return_value.raise_for_status.assert_called_once()

    def test_empty_list(self):
        with patch("WHartTest_tools.requests.get") as get:
            get.return_value = Mock(json=lambda: {"data": {"count": 0, "results": []}})
            self.assertEqual(json.loads(get_the_list_of_use_cases(13, 52)), [])

    def test_unexpected_schema_not_reported_as_no_cases(self):
        with patch("WHartTest_tools.requests.get") as get:
            get.return_value = Mock(json=lambda: {"data": "invalid"})
            with self.assertRaises(ValueError):
                get_the_list_of_use_cases(13, 52)


if __name__ == "__main__":
    unittest.main()
