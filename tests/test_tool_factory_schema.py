"""Minimal internal schema validator tests."""
from app.tool_factory.schema import validate_schema, validate_input, SchemaError


class TestSchemaValidation:
    def test_valid_schema(self):
        schema = {"type": "object", "properties": {"q": {"type": "string", "maxLength": 10}}, "required": ["q"]}
        assert validate_schema(schema) == []

    def test_unsupported_type_rejected(self):
        problems = validate_schema({"type": "weird"})
        assert problems

    def test_bad_regex_pattern_rejected(self):
        problems = validate_schema({"type": "string", "pattern": "([unclosed"})
        assert problems

    def test_required_not_in_properties(self):
        problems = validate_schema({"type": "object", "properties": {}, "required": ["missing"]})
        assert problems

    def test_non_object_schema_rejected(self):
        problems = validate_schema("not a dict")
        assert problems


class TestInputValidation:
    SCHEMA = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 1000}}}

    def test_valid_input(self):
        assert validate_input(self.SCHEMA, {"n": 50}) == []

    def test_wrong_type(self):
        problems = validate_input(self.SCHEMA, {"n": "fifty"})
        assert problems

    def test_out_of_range(self):
        assert validate_input(self.SCHEMA, {"n": 0})
        assert validate_input(self.SCHEMA, {"n": 5000})

    def test_required_missing(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
        assert validate_input(schema, {})

    def test_enum_violation(self):
        schema = {"type": "string", "enum": ["a", "b"]}
        assert validate_input(schema, "c")
        assert validate_input(schema, "a") == []

    def test_pattern_violation(self):
        schema = {"type": "string", "pattern": "^[a-z]+$"}
        assert validate_input(schema, "UPPER")
        assert validate_input(schema, "lower") == []

    def test_fails_closed_on_unknown_value(self):
        assert validate_input(self.SCHEMA, "not an object at all")
