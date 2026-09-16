import json
import os
import re
import uuid

import requests

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
OPENCODE_GO_URL = "https://opencode.ai/zen/go/v1/chat/completions"


def _extract_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


class MockLLM:
    def __init__(self, relations=None):
        self.relations = relations or [
            {"type": "yes_no", "condition_id": "mock-condition", "note": "mock"}
        ]

    def propose(self, system, user):
        proposal = {
            "mechanism": "Mock: yes/no pairs on the same market must sum to >= 1",
            "counterparty": "Mock counterparty",
            "why_not_arbitraged": "Mock",
            "falsification": "Mock: any resolved yes/no pair with sum != 1",
            "target_market": "mock",
            "relations": self.relations,
        }
        return proposal


class OpenAILLM:
    def __init__(self, model=None, base_url=None, api_key=None, extra_headers=None):
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.api_key = api_key or os.environ["OPENAI_API_KEY"]
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", OPENAI_URL)
        self.extra_headers = extra_headers or {}

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

    def propose(self, system, user, timeout=240, retries=1):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload = {"model": self.model, "messages": messages, "temperature": 0.4}
        headers = self._headers()
        last_err = None
        for attempt in range(retries + 1):
            try:
                payload["response_format"] = {"type": "json_object"}
                r = requests.post(self.base_url, headers=headers, json=payload, timeout=timeout)
                r.raise_for_status()
                break
            except requests.HTTPError:
                payload.pop("response_format", None)
                r = requests.post(self.base_url, headers=headers, json=payload, timeout=timeout)
                r.raise_for_status()
                break
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                if attempt >= retries:
                    raise
        else:
            raise last_err
        text = r.json()["choices"][0]["message"]["content"]
        return _extract_json(text)


class AnthropicLLM:
    def __init__(self, model=None):
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self.api_key = os.environ["ANTHROPIC_API_KEY"]

    def propose(self, system, user):
        r = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4000,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=120,
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
        return _extract_json(text)


class DeepSeekLLM(OpenAILLM):
    def __init__(self, model=None):
        super().__init__(
            model=model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_URL),
            api_key=os.environ["DEEPSEEK_API_KEY"],
        )


class OpenCodeGoLLM(OpenAILLM):
    def __init__(self, model=None, session_id=None):
        self.session_id = session_id or os.environ.get(
            "OPENCODE_GO_SESSION", str(uuid.uuid4())
        )
        super().__init__(
            model=model or os.environ.get("OPENCODE_GO_MODEL", "deepseek-v4-flash"),
            base_url=os.environ.get("OPENCODE_GO_BASE_URL", OPENCODE_GO_URL),
            api_key=os.environ["OPENCODE_API_KEY"],
            extra_headers={
                "User-Agent": "trading-lab-research/1.0",
                "x-opencode-session": self.session_id,
            },
        )


def get_llm():
    if os.environ.get("OPENCODE_API_KEY"):
        return OpenCodeGoLLM()
    if os.environ.get("DEEPSEEK_API_KEY"):
        return DeepSeekLLM()
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAILLM()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM()
    return MockLLM()