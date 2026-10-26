import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Protocol

_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits][::-1]
    total = sum(n if i % 2 == 0 else (n * 2 - 9 if n * 2 > 9 else n * 2) for i, n in enumerate(nums))
    return total % 10 == 0


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: re.Pattern
    group: int = 0
    validate: Callable[[str], bool] | None = None


RULES: list[Rule] = [
    Rule("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule("AWS_SECRET_KEY", re.compile(r"(?i)aws.{0,20}?(?:secret|private).{0,20}?[:=]\s*[\"']?([A-Za-z0-9/+=]{40})\b"), 1),
    Rule("GITHUB_TOKEN", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    Rule("SLACK_TOKEN", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    Rule("LLM_API_KEY", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}\b")),
    Rule("STRIPE_KEY", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    Rule("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    Rule("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    Rule("CONNECTION_PASSWORD", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@\"']+:([^\s@/\"']+)@"), 1),
    Rule("CREDENTIAL", re.compile(
        r"""(?i)\b(?:password|passwd|pwd|secret|client_secret|api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key)\b"""
        r"""["']?\s*[:=]\s*["']([^"'\s]{6,})["']"""), 1,
        validate=lambda v: not re.search(r"(?i)^(\$\{|\{\{|<|%\(|changeme|example|placeholder|xxx+|\*+$)", v)),
    Rule("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
         validate=lambda v: not v.lower().endswith(("@example.com", "@example.org", "@test.com", "@localhost"))),
    Rule("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b"),
         validate=lambda v: _luhn_ok(re.sub(r"\D", "", v)) and len(set(re.sub(r"\D", "", v))) > 1),
    Rule("US_SSN", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    Rule("PHONE", re.compile(r"(?<![\w.])\+?\d{1,3}[ .-]?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")),
    Rule("IP_ADDRESS", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
         validate=lambda v: v not in {"0.0.0.0", "127.0.0.1", "255.255.255.255"} and not v.startswith(("0.", "127."))),
]


class PIIEngine(Protocol):

    def analyze(self, text: str) -> list[tuple[int, int, str]]: ...


class PresidioEngine:

    ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "IBAN_CODE", "IP_ADDRESS"]

    def __init__(self, analyzer=None, threshold: float = 0.6):
        if analyzer is None:
            from presidio_analyzer import AnalyzerEngine  # type: ignore[import-not-found]

            analyzer = AnalyzerEngine()
        self.analyzer, self.threshold = analyzer, threshold

    def analyze(self, text: str) -> list[tuple[int, int, str]]:
        res = self.analyzer.analyze(text=text, entities=self.ENTITIES, language="en")
        return [(r.start, r.end, r.entity_type) for r in res if r.score >= self.threshold]


@dataclass
class Sanitizer:
    engine: PIIEngine | None = None
    counts: Counter = field(default_factory=Counter)
    _tokens: dict[tuple[str, str], str] = field(default_factory=dict)

    def _token(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self._tokens:
            self._tokens[key] = f"<REDACTED:{kind}_{sum(1 for k in self._tokens if k[0] == kind) + 1}>"
        self.counts[kind] += 1
        return self._tokens[key]

    def _line(self, line: str) -> str:
        for rule in RULES:
            def repl(m: re.Match, rule=rule) -> str:
                value = m.group(rule.group)
                if value is None or (rule.validate and not rule.validate(value)):
                    return m.group(0)
                s, e = m.start(rule.group) - m.start(0), m.end(rule.group) - m.start(0)
                whole = m.group(0)
                return whole[:s] + self._token(rule.kind, value) + whole[e:]
            line = rule.pattern.sub(repl, line)
        if self.engine:
            for start, end, kind in sorted(self.engine.analyze(line), key=lambda t: -t[0]):
                if "<REDACTED:" in line[max(0, start - 10):end]:
                    continue
                line = line[:start] + self._token(kind, line[start:end]) + line[end:]
        return line

    def sanitize(self, text: str) -> str:
        out, in_key = [], False
        for line in text.split("\n"):
            if _PRIVATE_KEY_BEGIN.search(line):
                out.append(self._token("PRIVATE_KEY", f"key#{self.counts['PRIVATE_KEY']}"))
                in_key = not _PRIVATE_KEY_END.search(line)
                continue
            if in_key:
                in_key = not _PRIVATE_KEY_END.search(line)
                out.append("")
                continue
            out.append(self._line(line))
        return "\n".join(out)

    def report(self) -> dict[str, int]:
        return dict(self.counts)


class EgressBlocked(Exception):
    pass


def egress_guard(payload: str) -> None:
    for rule in RULES[:8]:
        if rule.pattern.search(payload):
            raise EgressBlocked(f"{rule.kind} detected in outbound LLM payload")
    if _PRIVATE_KEY_BEGIN.search(payload):
        raise EgressBlocked("private key detected in outbound LLM payload")


def make_sanitizer(engine_name: str) -> Sanitizer:
    return Sanitizer(engine=PresidioEngine() if engine_name == "presidio" else None)
