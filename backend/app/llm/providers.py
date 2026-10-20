import json
import time
from dataclasses import dataclass

import httpx

from ..config import get_settings

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "line_start": {"type": "integer"},
            "line_end": {"type": "integer"},
            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
            "category": {"type": "string", "enum": ["security", "architecture", "style", "bug"]},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "suggestion": {"type": "string"},
            "rule_ref": {"type": "string"},
        },
        "required": ["file_path", "line_start", "line_end", "severity", "category", "title", "description",
                     "suggestion", "rule_ref"],
        "additionalProperties": False,
    }}},
    "required": ["issues"],
    "additionalProperties": False,
}


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: str = "error"):
        super().__init__(message)
        self.retryable, self.status = retryable, status


@dataclass
class Completion:
    data: dict
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


def _parse(text: str) -> dict:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        raise ProviderError(f"invalid JSON from model: {e}", retryable=False, status="invalid_output") from e
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ProviderError("model output missing 'issues'", retryable=False, status="invalid_output")
    return data


class AnthropicProvider:
    name = "anthropic"

    def __init__(self):
        self.model = get_settings().anthropic_model

    @staticmethod
    def available() -> bool:
        return bool(get_settings().anthropic_api_key)

    def complete(self, system: str, user: str, params: dict) -> Completion:
        import anthropic

        s = get_settings()
        client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.llm_timeout_s, max_retries=0)
        t0 = time.monotonic()
        try:
            resp = client.beta.messages.create(
                model=self.model,
                max_tokens=int(params.get("max_tokens", 16000)),
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": REVIEW_SCHEMA},
                               "effort": params.get("effort", s.anthropic_effort)},
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},
            )
        except anthropic.RateLimitError as e:
            raise ProviderError(f"rate limited: {e.message}", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"HTTP {e.status_code}: {e.message}", retryable=e.status_code >= 500 or e.status_code == 408) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"connection error: {e}", retryable=True) from e
        latency = int((time.monotonic() - t0) * 1000)
        usage = (resp.usage.input_tokens or 0, resp.usage.output_tokens or 0)
        if resp.stop_reason == "refusal":
            raise ProviderError("model declined the request (refusal)", retryable=False, status="refusal")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("output truncated at max_tokens", retryable=False, status="invalid_output")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return Completion(_parse(text), resp.model, *usage, latency)


class OllamaProvider:
    name = "ollama"
    transport: httpx.BaseTransport | None = None

    def __init__(self):
        self.model = get_settings().ollama_model

    @staticmethod
    def available() -> bool:
        return bool(get_settings().ollama_url)

    def complete(self, system: str, user: str, params: dict) -> Completion:
        s = get_settings()
        t0 = time.monotonic()
        try:
            with httpx.Client(base_url=s.ollama_url, timeout=s.llm_timeout_s, transport=self.transport) as c:
                r = c.post("/api/chat", json={
                    "model": self.model, "stream": False, "format": REVIEW_SCHEMA,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "options": {"temperature": 0, "num_ctx": 32768}})
        except httpx.HTTPError as e:
            raise ProviderError(f"ollama unreachable: {e}", retryable=True) from e
        if r.status_code >= 400:
            raise ProviderError(f"ollama HTTP {r.status_code}", retryable=r.status_code >= 500)
        body = r.json()
        return Completion(_parse(body.get("message", {}).get("content", "")), self.model,
                          int(body.get("prompt_eval_count", 0)), int(body.get("eval_count", 0)),
                          int((time.monotonic() - t0) * 1000))


class GeminiProvider:
    name = "gemini"
    transport: httpx.BaseTransport | None = None
    BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}

    def __init__(self):
        self.model = get_settings().gemini_model

    @staticmethod
    def available() -> bool:
        return bool(get_settings().gemini_api_key)

    def complete(self, system: str, user: str, params: dict) -> Completion:
        s = get_settings()
        t0 = time.monotonic()
        try:
            with httpx.Client(base_url=s.gemini_api_url, timeout=s.llm_timeout_s, transport=self.transport) as c:
                r = c.post(f"/v1beta/models/{self.model}:generateContent", headers={"x-goog-api-key": s.gemini_api_key}, json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"temperature": 0, "maxOutputTokens": int(params.get("max_tokens", 16000)),
                                         "responseMimeType": "application/json", "responseJsonSchema": REVIEW_SCHEMA}})
        except httpx.HTTPError as e:
            raise ProviderError(f"gemini unreachable: {e}", retryable=True) from e
        if r.status_code >= 400:
            raise ProviderError(f"gemini HTTP {r.status_code}", retryable=r.status_code >= 500 or r.status_code in (408, 429))
        body = r.json()
        usage = body.get("usageMetadata", {})
        tokens = (int(usage.get("promptTokenCount", 0)),
                  int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0)))
        if body.get("promptFeedback", {}).get("blockReason"):
            raise ProviderError("model declined the request (prompt blocked)", retryable=False, status="refusal")
        cand = (body.get("candidates") or [{}])[0]
        if cand.get("finishReason") in self.BLOCKED:
            raise ProviderError(f"model declined the request ({cand['finishReason']})", retryable=False, status="refusal")
        if cand.get("finishReason") == "MAX_TOKENS":
            raise ProviderError("output truncated at max_tokens", retryable=False, status="invalid_output")
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        return Completion(_parse(text), body.get("modelVersion", self.model), *tokens, int((time.monotonic() - t0) * 1000))


REGISTRY = {"anthropic": AnthropicProvider, "ollama": OllamaProvider, "gemini": GeminiProvider}


def configured() -> list:
    return [REGISTRY[n]() for n in get_settings().provider_order if n in REGISTRY and REGISTRY[n].available()]
