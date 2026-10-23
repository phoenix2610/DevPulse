import json
import shutil
import subprocess
from pathlib import Path

from .config import get_settings

_CHECK = r"""
import ast, json, os, sys
out = []
for d, ds, fs in os.walk('/src'):
    ds[:] = [x for x in ds if x not in ('.git', 'node_modules', 'venv', '.venv')]
    for f in fs:
        if f.endswith('.py'):
            p = os.path.join(d, f)
            try:
                ast.parse(open(p, encoding='utf-8', errors='replace').read())
            except SyntaxError as e:
                out.append({'path': os.path.relpath(p, '/src'), 'line': e.lineno or 1, 'msg': e.msg})
print(json.dumps(out[:200]))
"""


def available() -> bool:
    return bool(get_settings().sandbox_enabled and shutil.which("docker"))


def python_syntax_check(root: Path) -> list[dict]:
    s = get_settings()
    if s.sandbox_repos_volume:
        mount = ["--mount", f"type=volume,src={s.sandbox_repos_volume},dst=/repos,readonly"]
        target = f"/repos/{root.name}"
    else:
        mount, target = ["-v", f"{root}:/src:ro"], "/src"
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "--memory", "256m", "--cpus", "1", "--pids-limit", "64",
           "--user", "65534:65534", *mount, s.sandbox_image, "python", "-c", _CHECK.replace("'/src'", repr(target))]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[-300:] or "sandbox failed")
    return [{"file_path": r["path"], "line_start": r["line"], "line_end": r["line"], "severity": "high", "category": "bug",
             "agent": "sandbox", "source": "sandbox", "title": "Python syntax error", "description": r["msg"],
             "suggestion": "Fix the syntax error; the file cannot be imported.", "rule_ref": None}
            for r in json.loads(res.stdout or "[]")]
