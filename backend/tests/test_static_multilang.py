import shutil
import subprocess

import pytest

from app.agents import semgrep_runner
from conftest import URL

JS = '''const cp = require('child_process');
function run(user) { cp.exec("ls " + user); }
function safe() { cp.exec("ls -la"); }
db.query(`SELECT * FROM t WHERE id=${id}`);
db.query("SELECT * FROM t WHERE id=?", [id]);
el.innerHTML = userInput;
'''


@pytest.mark.skipif(not semgrep_runner.available(), reason="semgrep not installed")
def test_semgrep_findings_in_job_without_regex_duplicates(client, auth, fake_clone):
    src, _ = fake_clone
    (src / "web").mkdir()
    (src / "web" / "app.js").write_text(JS)
    h, _ = auth
    job_id = client.post("/jobs", json={"repo_url": URL}, headers=h).json()["job_id"]
    js = [i for i in client.get(f"/jobs/{job_id}/issues", headers=h).json() if i["file_path"] == "web/app.js"]
    got = {(i["line_start"], i["title"]) for i in js}
    assert (2, "child_process.exec with dynamic input") in got and (4, "SQL built from dynamic string") in got
    assert not any(line in (3, 5) for line, _ in got)
    assert sum(1 for line, _ in got if line == 6) == 1
    assert all(i["agent"] == "static-semgrep" for i in js if i["category"] == "security")


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", "python:3.12-slim"], capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="docker daemon + python:3.12-slim image required")
def test_sandbox_runs_untrusted_code_checks_isolated(tmp_path, monkeypatch):
    from app import sandbox
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / "bad.py").write_text("def f(:\n    pass\n")
    tmp_path.chmod(0o755)
    issues = sandbox.python_syntax_check(tmp_path)
    assert [(i["file_path"], i["line_start"], i["source"]) for i in issues] == [("bad.py", 1, "sandbox")]
