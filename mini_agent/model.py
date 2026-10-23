from __future__ import annotations

import json
import re
from typing import Any

import httpx


class OpenAICompatModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "local",
        timeout: float = 120.0,
        verbose: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.verbose = verbose
        self.last_usage: dict[str, int] = {}
        self.temperature = 0.1

    def chat(self, system: str, user: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.verbose:
            print(f"[verbose] POST {url}")
            print(f"[verbose] model={self.model} temperature={payload['temperature']}")
            print(f"[verbose] system_prompt length={len(system)} chars")
            print(f"[verbose] user_prompt length={len(user)} chars")
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(url, json=payload, headers=headers)
            if self.verbose:
                print(
                    f"[verbose] HTTP {r.status_code} in {r.elapsed.total_seconds():.2f}s"
                )
            r.raise_for_status()
            data = r.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        self.last_usage = usage if isinstance(usage, dict) else {}
        if self.verbose:
            usage = data.get("usage", {})
            print(f"[verbose] response length={len(content)} chars")
            if usage:
                print(
                    f"[verbose] tokens: prompt={usage.get('prompt_tokens', '?')} completion={usage.get('completion_tokens', '?')} total={usage.get('total_tokens', '?')}"
                )
            print(f"[verbose] raw response:\n{content}")
        return content


def _first_json_object(text: str) -> str | None:
    """Return the first balanced JSON object in text, string-aware."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def extract_json_object(text: str) -> dict[str, Any]:
    text = _strip_code_fence(text)

    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        obj = _first_json_object(text)
        if obj is None:
            raise ValueError(f"Model did not return JSON: {text[:500]}")
        return json.loads(obj, strict=False)


def validate_action_object(action: object) -> str:
    """Return an error message when an action object violates the contract.

    The canonical field is "action" and it must be a non-empty string.
    Missing or invalid actions are structured model-output failures and
    are never silently converted to an empty string.
    """
    if not isinstance(action, dict):
        return "Model output is not a JSON object."
    if "action" not in action:
        if "next_action" in action:
            return (
                "Model used the field 'next_action' instead of the canonical "
                "'action' field. Use exactly: {\"action\": \"<allowed action>\"}"
            )
        return (
            "Model output is missing the required 'action' field. "
            "Return exactly one JSON object with an 'action' field naming "
            "a single allowed action."
        )
    act = action["action"]
    if not isinstance(act, str) or not act:
        return (
            "Model 'action' field must be a non-empty string. "
            "Return exactly one JSON object with an 'action' field."
        )
    return ""


def _extract_patch_lenient(text: str) -> dict[str, Any] | None:
    """Last-resort repair for patch actions with broken JSON strings.

    Small models emit correct unified diffs but break the JSON encoding
    (typically unescaped quotes inside the patch string). When the whole
    output fails to parse, locate the "patch" value directly: from the
    opening quote to the last quote whose tail is only closing syntax.
    """
    m = re.search(r'"patch"\s*:\s*"', text)
    if not m:
        return None
    start = m.end()
    for end in range(len(text) - 1, start, -1):
        if text[end] != '"':
            continue
        if text[end + 1 :].strip().strip("}],").strip() == "":
            patch = text[start:end]
            if "\\n" in patch and "\n" not in patch:
                patch = (
                    patch.replace("\\n", "\n")
                    .replace('\\"', '"')
                    .replace("\\\\", "\\")
                )
            return {"action": "apply_patch", "patch": patch}
    return None


def parse_model_output(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse raw model output into (action_dict, error_message).

    Returns (None, error_message) for malformed JSON and for objects that
    violate the canonical action contract. A patch action whose JSON is
    broken only inside the patch string is repaired leniently.
    """
    try:
        action = extract_json_object(text)
    except ValueError as exc:
        action = _extract_patch_lenient(_strip_code_fence(text))
        if action is None:
            return None, f"Model output is not valid JSON: {exc}"
    error = validate_action_object(action)
    if error:
        return None, error
    return action, None
