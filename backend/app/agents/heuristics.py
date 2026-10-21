import re
from pathlib import Path

import logging

from . import semgrep_runner
from .ast_analyzer import analyze_python
from .repo import RepoSnapshot, read_text

SECRET_RULES = [
    ("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "critical"),
    ("Private key material", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "critical"),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "critical"),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "high"),
    ("Anthropic/OpenAI style API key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{24,}\b"), "critical"),
    ("Hardcoded credential", re.compile(r"""(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*["'][^"'\s]{8,}["']"""), "high"),
]
PLACEHOLDER = re.compile(r"(?i)(example|changeme|change-me|placeholder|your[_-]|xxxx|<.*>|\$\{|os\.environ|getenv|process\.env)")

CODE_RULES = [
    ("Use of eval/exec on dynamic input", re.compile(r"(?<![\w.])(?:eval|exec)\s*\("), "high", "A03 Injection",
     "Avoid eval/exec; use ast.literal_eval or explicit dispatch tables.", {"python", "javascript", "typescript"}),
    ("Shell command with shell=True", re.compile(r"shell\s*=\s*True"), "high", "A03 Injection",
     "Pass an argument list to subprocess without shell=True.", {"python"}),
    ("os.system call", re.compile(r"\bos\.system\s*\("), "high", "A03 Injection",
     "Use subprocess.run with an argument list.", {"python"}),
    ("Unsafe deserialization (pickle/yaml.load)", re.compile(r"\b(?:pickle\.loads?|yaml\.load)\s*\((?!.*SafeLoader)"), "high",
     "A08 Software and Data Integrity Failures", "Use json or yaml.safe_load; never unpickle untrusted data.", {"python"}),
    ("SQL built with string formatting", re.compile(r"""(?i)\b(?:execute|query)\s*\(\s*(?:f["']|["'][^"']*(?:SELECT|INSERT|UPDATE|DELETE)[^"']*["']\s*(?:%|\+|\.format))"""),
     "high", "A03 Injection", "Use parameterized queries.", {"python", "javascript", "typescript", "java", "php"}),
    ("Weak hash (MD5/SHA1)", re.compile(r"(?i)\b(?:hashlib\.(?:md5|sha1)|createHash\(['\"](?:md5|sha1)['\"]\))"), "medium",
     "A02 Cryptographic Failures", "Use SHA-256+ (and a KDF such as argon2/bcrypt for passwords).", {"python", "javascript", "typescript"}),
    ("TLS verification disabled", re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true"), "high",
     "A02 Cryptographic Failures", "Keep certificate verification enabled.", {"python", "javascript", "typescript", "go"}),
    ("Debug mode enabled", re.compile(r"(?i)\bdebug\s*=\s*True\b|app\.run\([^)]*debug\s*=\s*True"), "medium",
     "A05 Security Misconfiguration", "Disable debug in production via environment config.", {"python"}),
    ("Unescaped HTML sink", re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*="), "medium", "A03 Injection",
     "Sanitize with a trusted library (e.g. DOMPurify) or render as text.", {"javascript", "typescript"}),
    ("Permissive CORS", re.compile(r"""(?i)allow_origins\s*=\s*\[\s*["']\*["']|Access-Control-Allow-Origin["']?\s*[:,]\s*["']\*"""), "medium",
     "A05 Security Misconfiguration", "Restrict origins to a known allowlist.", {"python", "javascript", "typescript"}),
    ("Bare except / swallowed error", re.compile(r"^\s*except\s*:\s*$|catch\s*\([^)]*\)\s*\{\s*\}"), "low", "Error handling",
     "Catch specific exceptions and log them.", {"python", "javascript", "typescript", "java"}),
]

AST_COVERED = {"Use of eval/exec on dynamic input", "Shell command with shell=True", "os.system call",
               "Unsafe deserialization (pickle/yaml.load)", "SQL built with string formatting", "Weak hash (MD5/SHA1)",
               "TLS verification disabled", "Bare except / swallowed error", "Unescaped HTML sink"}


def _issue(path, line, sev, cat, agent, title, desc, sugg=None, ref=None, end=None):
    return {"file_path": path, "line_start": line, "line_end": end or line, "severity": sev, "category": cat,
            "agent": agent, "title": title, "description": desc, "suggestion": sugg, "rule_ref": ref, "source": "static"}


log = logging.getLogger(__name__)


def security_scan(snap: RepoSnapshot) -> list[dict]:
    issues: list[dict] = []
    ast_langs = {"python"}
    if semgrep_runner.available() and any(f.language in semgrep_runner.LANGS for f in snap.files):
        try:
            issues += semgrep_runner.scan(snap.root)
            ast_langs |= semgrep_runner.LANGS
        except (RuntimeError, OSError, ValueError, TimeoutError) as e:
            log.warning("semgrep failed, using regex rules: %s", e)
    for f in snap.files:
        text = read_text(snap.root / f.path)
        if text is None:
            continue
        if f.language == "python":
            issues += [i for i in analyze_python(f.path, text) if i["category"] in ("security", "bug")]
        for i, line in enumerate(text.splitlines(), 1):
            if len(line) > 2000:
                continue
            for name, rx, sev in SECRET_RULES:
                if rx.search(line) and not PLACEHOLDER.search(line):
                    issues.append(_issue(f.path, i, sev, "security", "security-reviewer", f"Exposed secret: {name}",
                                         "A credential appears to be committed to source control.",
                                         "Remove it, rotate the credential, and load it from environment/secret manager.",
                                         "A02 Cryptographic Failures"))
            for title, rx, sev, ref, sugg, langs in CODE_RULES:
                if f.language in langs and not (f.language in ast_langs and title in AST_COVERED) and rx.search(line):
                    issues.append(_issue(f.path, i, sev, "security", "security-reviewer", title,
                                         f"Pattern flagged by static rule ({ref}).", sugg, ref))
    return issues


_FUNC = re.compile(r"^\s*(?:async\s+)?(?:def|function)\s+(\w+)|^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(")


def architecture_scan(snap: RepoSnapshot) -> list[dict]:
    issues: list[dict] = []
    names = {f.path.lower() for f in snap.files}
    code = [f for f in snap.files if f.language not in {"markdown", "json", "yaml", "toml", "dotenv"}]
    if not any(n.endswith("readme.md") for n in names):
        issues.append(_issue("README.md", 1, "low", "architecture", "architecture-reviewer", "Missing README",
                             "No README found describing setup and architecture.", "Add a README.", "Testing and documentation"))
    if code and not any(re.search(r"(^|/)(tests?|__tests__)/|\.test\.|\.spec\.|test_.*\.py", n) for n in names):
        issues.append(_issue(code[0].path, 1, "medium", "architecture", "architecture-reviewer", "No automated tests detected",
                             "The repository has no recognizable test files.", "Add unit tests for core logic.",
                             "Testing and documentation"))
    for f in code:
        if f.lines > 800:
            issues.append(_issue(f.path, 1, "medium", "architecture", "architecture-reviewer", "Very large file",
                                 f"{f.lines} lines; likely mixes responsibilities.", "Split into cohesive modules.",
                                 "Function size and complexity", end=min(f.lines, 800)))
        text = read_text(snap.root / f.path) or ""
        lines = text.splitlines()
        if f.language == "python":
            issues += [i for i in analyze_python(f.path, text) if i["category"] == "architecture"]
            starts = []
        else:
            starts = None
        if starts is None:
            starts = [(i, m.group(1) or m.group(2)) for i, ln in enumerate(lines, 1) if (m := _FUNC.match(ln))]
        for (a, name), nxt in zip(starts, [s[0] for s in starts[1:]] + [len(lines) + 1]):
            if nxt - a > 80:
                issues.append(_issue(f.path, a, "low", "architecture", "architecture-reviewer", f"Long function `{name}`",
                                     f"~{nxt - a} lines long.", "Extract helpers to reduce complexity.",
                                     "Function size and complexity", end=nxt - 1))
        for i, ln in enumerate(lines, 1):
            if re.search(r"\b(TODO|FIXME|HACK)\b", ln) and f.language != "markdown":
                issues.append(_issue(f.path, i, "info", "style", "architecture-reviewer", "Unresolved TODO/FIXME",
                                     ln.strip()[:200], "Resolve or track in an issue.", "Testing and documentation"))
    return issues
