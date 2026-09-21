"""Minimal internal JSON-schema validator.

Supports exactly the subset the Tool Factory needs — type, required, enum,
min/max, minLength/maxLength, pattern — with no external dependency.
Fails closed: unknown constructs or malformed schemas reject the input
(or the schema itself, in validate_schema()).
"""
from __future__ import annotations

import re
from typing import Any

MAX_PATTERN_LENGTH = 200

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


class SchemaError(ValueError):
    """The schema itself is invalid for Tool Factory use."""


def validate_schema(schema: Any, root_name: str = "schema") -> list[str]:
    """Return a list of schema problems (empty = valid Tool Factory schema)."""
    problems: list[str] = []
    _check_schema_node(schema, root_name, problems, depth=0)
    return problems


def _check_schema_node(node: Any, path: str, problems: list[str], depth: int) -> None:
    if depth > 6:
        problems.append(f"{path}: schemas nested deeper than 6 levels are not supported")
        return
    if node is None:
        return  # unconstrained
    if not isinstance(node, dict):
        problems.append(f"{path}: schema must be an object")
        return
    declared = node.get("type")
    if declared is not None and declared not in _TYPE_CHECKS:
        problems.append(f"{path}: unsupported schema type '{declared}'")
    for constraint in ("minLength", "maxLength"):
        if constraint in node and (not isinstance(node[constraint], int) or isinstance(node[constraint], bool) or node[constraint] < 0):
            problems.append(f"{path}: {constraint} must be a non-negative integer")
    for constraint in ("minimum", "maximum"):
        if constraint in node and (isinstance(node[constraint], bool) or not isinstance(node[constraint], (int, float))):
            problems.append(f"{path}: {constraint} must be a number")
    if "pattern" in node:
        if not isinstance(node["pattern"], str) or len(node["pattern"]) > MAX_PATTERN_LENGTH:
            problems.append(f"{path}: pattern must be a string of at most {MAX_PATTERN_LENGTH} characters")
        else:
            try:
                re.compile(node["pattern"])
            except re.error:
                problems.append(f"{path}: pattern is not a valid regex")
    if "enum" in node and not isinstance(node["enum"], list):
        problems.append(f"{path}: enum must be a list")
    if "items" in node and not isinstance(node["items"], (dict, type(None))):
        problems.append(f"{path}: items must be an object schema")
    if "properties" in node:
        if not isinstance(node["properties"], dict):
            problems.append(f"{path}: properties must be an object")
        else:
            for name, child in node["properties"].items():
                _check_schema_node(child, f"{path}.properties.{name}", problems, depth + 1)
    if "required" in node:
        required = node["required"]
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            problems.append(f"{path}: required must be a list of strings")
        elif isinstance(node.get("properties"), dict):
            missing = [item for item in required if item not in node["properties"]]
            if missing:
                problems.append(f"{path}: required names not in properties: {missing}")


def validate_input(schema: dict, value: Any, path: str = "input") -> list[str]:
    """Validate a runtime input value against a Tool Factory schema.

    Returns problem descriptions (empty = valid). Unknown value shapes fail
    closed against declared types.
    """
    if not isinstance(schema, dict):
        return [f"{path}: schema is not a valid object"]
    problems: list[str] = []
    _check_value(schema, value, path, problems)
    return problems


def _check_value(schema: dict, value: Any, path: str, problems: list[str]) -> None:
    declared = schema.get("type")
    if declared is not None:
        check = _TYPE_CHECKS.get(declared)
        if check is None or not check(value):
            problems.append(f"{path}: expected {declared}, got {type(value).__name__}")
            return
    if value is None:
        return
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: value not in allowed set")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            problems.append(f"{path}: does not match required pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        if len(value) > 100:
            problems.append(f"{path}: arrays are limited to 100 items")
            return
        for index, item in enumerate(value):
            _check_value(schema["items"], item, f"{path}[{index}]", problems)
    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        for name, child in schema["properties"].items():
            if name in value:
                _check_value(child, value[name], f"{path}.{name}", problems)
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{path}.{name}: required property missing")
