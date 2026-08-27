import pytest

from mini_agent.model import extract_json_object


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
