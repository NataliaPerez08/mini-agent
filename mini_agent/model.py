from __future__ import annotations

import json
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
            "temperature": 0.1,
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


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"Model did not return JSON: {text[:500]}")
        return json.loads(text[start : end + 1])
