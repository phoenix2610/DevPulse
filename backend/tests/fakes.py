import json
import re

from app.llm.providers import Completion, ProviderError


class FakeProvider:

    def __init__(self, name="anthropic", script=("ok",), model="claude-opus-5-5", issues=None):
        self.name, self.model, self.script, self.calls = name, model, list(script), []
        self.issues = issues

    def complete(self, system, user, params):
        self.calls.append({"system": system, "user": user, "params": params})
        step = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if step == "retry":
            raise ProviderError("HTTP 529: overloaded", retryable=True)
        if step == "fatal":
            raise ProviderError("HTTP 400: bad request", retryable=False)
        if step == "refusal":
            raise ProviderError("refusal", retryable=False, status="refusal")
        files = re.findall(r"=== FILE: (\S+) ===", user)
        issues = self.issues if self.issues is not None else [{
            "file_path": files[0], "line_start": 1, "line_end": 1, "severity": "medium", "category": "security",
            "title": f"LLM finding via {self.name}", "description": "d", "suggestion": "s", "rule_ref": ""}]
        return Completion({"issues": issues}, self.model, 1200, 300, 42)


def install(monkeypatch, *providers):
    from app.llm import router
    monkeypatch.setattr(router, "configured", lambda: list(providers))
    return providers
