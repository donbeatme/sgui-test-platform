from typing import Any, Dict, Optional


EXPECTED_VALUE_TYPE_META_KEY = "__expected_value_type"
VALIDATOR_META_KEYS = {EXPECTED_VALUE_TYPE_META_KEY}

SUPPORTED_COMPARATORS = {
    "eq",
    "ne",
    "gt",
    "ge",
    "gte",
    "lt",
    "le",
    "lte",
    "contains",
    "contained_by",
    "type_match",
    "regex_match",
    "startswith",
    "endswith",
    "str_eq",
    "length_equal",
    "length_greater_than",
    "length_less_than",
    "length_greater_or_equals",
    "length_less_or_equals",
}


def is_validator_meta_key(key: str) -> bool:
    return key in VALIDATOR_META_KEYS


def prepare_validator_for_runtime(validator: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(validator, dict):
        return None

    runtime_validator = {}
    for key in VALIDATOR_META_KEYS:
        if key in validator:
            runtime_validator[key] = validator[key]

    if "check" in validator and "expect" in validator:
        for key in ("check", "expect", "assert", "comparator", "message", "msg"):
            if key in validator:
                runtime_validator[key] = validator[key]
        return runtime_validator

    for comparator, compare_values in validator.items():
        if is_validator_meta_key(comparator):
            continue
        if not isinstance(compare_values, list) or len(compare_values) < 2:
            continue
        runtime_validator[comparator] = compare_values[:3]
        return runtime_validator

    return None
