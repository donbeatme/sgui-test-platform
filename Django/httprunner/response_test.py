import unittest

import requests

from httprunner.parser import Parser
from httprunner.response import ResponseObject, ResponseObjectBase, uniform_validator
from httprunner.utils import HTTP_BIN_URL


class TestResponse(unittest.TestCase):
    def setUp(self) -> None:
        resp = requests.post(
            f"{HTTP_BIN_URL}/anything",
            json={
                "locations": [
                    {"name": "Seattle", "state": "WA"},
                    {"name": "New York", "state": "NY"},
                    {"name": "Bellevue", "state": "WA"},
                    {"name": "Olympia", "state": "WA"},
                ]
            },
        )
        parser = Parser(
            functions_mapping={"get_name": lambda: "name", "get_num": lambda x: x}
        )
        self.resp_obj = ResponseObject(resp, parser)

    def test_extract(self):
        variables_mapping = {"body": "body"}
        extract_mapping = self.resp_obj.extract(
            {
                "var_1": "body.json.locations[0]",
                "var_2": "body.json.locations[3].name",
                "var_3": "$body.json.locations[3].name",
                "var_4": "$body.json.locations[3].${get_name()}",
            },
            variables_mapping=variables_mapping,
        )
        self.assertEqual(extract_mapping["var_1"], {"name": "Seattle", "state": "WA"})
        self.assertEqual(extract_mapping["var_2"], "Olympia")
        self.assertEqual(extract_mapping["var_3"], "Olympia")
        self.assertEqual(extract_mapping["var_4"], "Olympia")

    def test_validate(self):
        self.resp_obj.validate(
            [
                {"eq": ["body.json.locations[0].name", "Seattle"]},
                {"eq": ["body.json.locations[0]", {"name": "Seattle", "state": "WA"}]},
            ],
        )

    def test_validate_variables(self):
        variables_mapping = {"index": 1, "var_empty": ""}
        self.resp_obj.validate(
            [
                {"eq": ["body.json.locations[$index].name", "New York"]},
                {"eq": ["$var_empty", ""]},
            ],
            variables_mapping=variables_mapping,
        )

    def test_validate_functions(self):
        variables_mapping = {"index": 1}
        self.resp_obj.validate(
            [
                {"eq": ["${get_num(0)}", 0]},
                {"eq": ["${get_num($index)}", 1]},
            ],
            variables_mapping=variables_mapping,
        )

    def test_uniform_validator(self):
        validators = [
            {
                "check": "status_code",
                "comparator": "eq",
                "expect": 201,
                "message": "test",
            },
            {"check": "status_code", "assert": "eq", "expect": 201, "msg": "test"},
            {"eq": ["status_code", 201, "test"]},
        ]
        expected = {
            "check": "status_code",
            "assert": "equal",
            "expect": 201,
            "message": "test",
            "expected_value_type": "",
        }
        for validator in validators:
            self.assertEqual(uniform_validator(validator), expected)

        self.assertEqual(
            uniform_validator({
                "eq": ["status_code", "${status_code}"],
                "__expected_value_type": "number",
            }),
            {
                "check": "status_code",
                "assert": "equal",
                "expect": "${status_code}",
                "message": "",
                "expected_value_type": "number",
            },
        )

class TestResponseExpectedValueTypes(unittest.TestCase):
    def test_uniform_validator_preserves_expected_value_type_meta(self):
        self.assertEqual(
            uniform_validator({
                "eq": ["status_code", "${status_code}"],
                "__expected_value_type": "number",
            }),
            {
                "check": "status_code",
                "assert": "equal",
                "expect": "${status_code}",
                "message": "",
                "expected_value_type": "number",
            },
        )

    def test_validate_coerces_declared_expected_value_type(self):
        resp_obj = ResponseObjectBase(
            {"body": {"count": 5, "message": "OK"}},
            Parser(functions_mapping={}),
        )

        resp_obj.validate(
            [{
                "eq": ["body.count", "${expected_count}"],
                "__expected_value_type": "number",
            }],
            variables_mapping={"expected_count": "5"},
        )
        validator = resp_obj.validation_results["validate_extractor"][0]
        self.assertTrue(resp_obj.validation_results["success"])
        self.assertEqual(validator["expect_value"], 5)
        self.assertEqual(validator["expect_value_type"], "int")
        self.assertEqual(validator["expect_declared_value_type"], "number")

    def test_validate_fails_when_declared_number_is_not_numeric(self):
        resp_obj = ResponseObjectBase(
            {"body": {"message": "OK"}},
            Parser(functions_mapping={}),
        )

        resp_obj.validate([
            {
                "eq": ["body.message", "OK"],
                "__expected_value_type": "number",
            }
        ])

        validator = resp_obj.validation_results["validate_extractor"][0]
        self.assertFalse(resp_obj.validation_results["success"])
        self.assertEqual(validator["check_result"], "fail")
        self.assertEqual(validator["expect_declared_value_type"], "number")
        self.assertIn("无法转换为数字", validator["message"])
