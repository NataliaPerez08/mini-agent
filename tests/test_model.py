import pytest

from mini_agent.model import (
    extract_json_object,
    parse_model_output,
    validate_action_object,
)


class TestExtractJsonObject:
    def test_plain_json(self):
        text = '{"action":"read_file","path":"src/app.py"}'
        result = extract_json_object(text)
        assert result == {"action": "read_file", "path": "src/app.py"}

    def test_json_with_surrounding_whitespace(self):
        text = '   \n  {"action":"finish","summary":"done"}  \n  '
        result = extract_json_object(text)
        assert result == {"action": "finish", "summary": "done"}

    def test_markdown_fenced_json(self):
        text = '```json\n{"action":"run_tests"}\n```'
        result = extract_json_object(text)
        assert result == {"action": "run_tests"}

    def test_markdown_fenced_without_language(self):
        text = '```\n{"action":"search_code","query":"foo"}\n```'
        result = extract_json_object(text)
        assert result == {"action": "search_code", "query": "foo"}

    def test_json_embedded_in_text(self):
        text = 'Here is my action:\n{"action":"read_file","path":"a.py"}\nThanks!'
        result = extract_json_object(text)
        assert result == {"action": "read_file", "path": "a.py"}

    def test_nested_braces_in_value(self):
        text = '{"action":"apply_patch","patch":"line with {nested} braces here"}'
        result = extract_json_object(text)
        assert result["action"] == "apply_patch"
        assert "{nested}" in result["patch"]

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            extract_json_object("")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError):
            extract_json_object("no json here at all")

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            extract_json_object("{broken")

    def test_json_with_text_around_braces(self):
        text = 'Here is the action {"action":"finish","summary":"done"} thanks'
        result = extract_json_object(text)
        assert result == {"action": "finish", "summary": "done"}


class TestValidateActionObject:
    def test_valid_action(self):
        assert validate_action_object({"action": "finish"}) == ""

    def test_non_object_returns_error(self):
        assert validate_action_object("finish") != ""
        assert validate_action_object([]) != ""
        assert validate_action_object(None) != ""

    def test_missing_action_returns_error(self):
        assert "action" in validate_action_object({"foo": "bar"})

    def test_next_action_rejected_with_hint(self):
        msg = validate_action_object({"next_action": "none"})
        assert validate_action_object({"next_action": "none"}) != ""
        assert "next_action" in msg
        assert "'action'" in msg

    def test_empty_action_string_rejected(self):
        assert validate_action_object({"action": ""}) != ""
        assert validate_action_object({"action": None}) != ""


class TestParseModelOutput:
    def test_valid_json(self):
        action, error = parse_model_output('{"action":"read_symbol","path":"a.go"}')
        assert error is None
        assert action["action"] == "read_symbol"

    def test_malformed_json(self):
        action, error = parse_model_output("not json at all")
        assert action is None
        assert error is not None
        assert "JSON" in error

    def test_empty_string(self):
        action, error = parse_model_output("")
        assert action is None
        assert error is not None

    def test_next_action_contract_violation(self):
        action, error = parse_model_output('{"next_action":"none"}')
        assert action is None
        assert error is not None
        assert "next_action" in error

    def test_missing_action_field(self):
        action, error = parse_model_output('{"summary":"done"}')
        assert action is None
        assert "action" in error

    def test_fenced_valid_json(self):
        action, error = parse_model_output('```json\n{"action":"run_tests"}\n```')
        assert error is None
        assert action["action"] == "run_tests"
