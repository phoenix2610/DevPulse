import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
RULES = Path(__file__).resolve().parents[2] / "rules" / "semgrep.yml"
LANGS = {"javascript", "typescript", "go", "java", "php", "ruby"}
SEVERITY = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}


def available() -> bool:
    return bool(shutil.which("semgrep")) and RULES.is_file()


def scan(root: Path, timeout_s: int = 300) -> list[dict]:
    cmd = ["semgrep", "scan", "--config", str(RULES), "--json", "--quiet", "--metrics=off", "--disable-version-check",
           "--no-git-ignore", "--timeout", "30", "--max-target-bytes", "1000000",
           "--exclude", "node_modules", "--exclude", "vendor", "--exclude", "dist", "--exclude", "build", str(root)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=root)
    if res.returncode not in (0, 1):
        raise RuntimeError(f"semgrep exited {res.returncode}: {res.stderr.strip()[-300:]}")
    out = []
    for r in json.loads(res.stdout or "{}").get("results", []):
        meta = r.get("extra", {}).get("metadata", {})
        path = Path(r["path"])
        rel = (path if not path.is_absolute() else path.relative_to(root)).as_posix()
        out.append({
            "file_path": rel, "line_start": r["start"]["line"], "line_end": max(r["start"]["line"], r["end"]["line"]),
            "severity": SEVERITY.get(r.get("extra", {}).get("severity", "WARNING"), "medium"),
            "category": meta.get("category", "security"), "agent": "static-semgrep",
            "title": meta.get("title") or r["check_id"].rsplit(".", 1)[-1],
            "description": r.get("extra", {}).get("message", ""), "suggestion": meta.get("suggestion"),
            "rule_ref": meta.get("owasp"), "source": "static",
        })
    return out
