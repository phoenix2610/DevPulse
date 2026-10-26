import pytest

from app.compliance.sanitizer import EgressBlocked, PresidioEngine, Sanitizer, egress_guard
from conftest import URL
from fakes import FakeProvider, install

SECRETS_SRC = '''DB_URL = "postgres://admin:hunter2pass@db.internal:5432/app"
GH = "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"
password = "S3cretValue!"
password = "${FROM_ENV}"
owner = "jane.doe@acme-corp.io"  # cc jane.doe@acme-corp.io
card = "4111 1111 1111 1111"
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAA
-----END OPENSSH PRIVATE KEY-----
ok = 1'''


def test_sanitizer_masks_and_preserves_lines():
    s = Sanitizer()
    out = s.sanitize(SECRETS_SRC)
    assert len(out.split("\n")) == len(SECRETS_SRC.split("\n"))
    for raw in ("hunter2pass", "ghp_abcdef", "S3cretValue", "jane.doe@", "4111 1111", "b3BlbnNzaC1rZXk"):
        assert raw not in out
    assert "${FROM_ENV}" in out and "ok = 1" in out
    assert out.count("<REDACTED:EMAIL_1>") == 2
    assert s.report() == {"CONNECTION_PASSWORD": 1, "GITHUB_TOKEN": 1, "CREDENTIAL": 1, "EMAIL": 2, "CREDIT_CARD": 1, "PRIVATE_KEY": 1}
    egress_guard(out)


def test_egress_guard_blocks_raw_secret():
    with pytest.raises(EgressBlocked):
        egress_guard('k = "AKIAIOSFODNN7ABCDEFG"')


def test_presidio_adapter_with_ner_engine():
    class R:
        def __init__(self, s, e, t, score): self.start, self.end, self.entity_type, self.score = s, e, t, score

    class FakeAnalyzer:
        def analyze(self, text, entities, language):
            i = text.find("Grace Hopper")
            return [R(i, i + 12, "PERSON", 0.85)] if i >= 0 else []

    s = Sanitizer(engine=PresidioEngine(analyzer=FakeAnalyzer()))
    assert s.sanitize("# author: Grace Hopper") == "# author: <REDACTED:PERSON_1>"


def test_no_raw_secret_reaches_llm(client, auth, fake_clone, monkeypatch):
    (src, _) = fake_clone
    (src / "pkg" / "settings.py").write_text(SECRETS_SRC)
    (p,) = install(monkeypatch, FakeProvider())
    job = client.get(f"/jobs/{client.post('/jobs', json={'repo_url': URL}, headers=auth[0]).json()['job_id']}",
                     headers=auth[0]).json()
    sent = "\n".join(c["user"] for c in p.calls)
    assert p.calls and "AKIAIOSFODNN7ABCDEFG" not in sent and "hunter2pass" not in sent and "jane.doe@" not in sent
    assert "<REDACTED:AWS_ACCESS_KEY_1>" in sent
    assert job["summary"]["redactions"]["AWS_ACCESS_KEY"] >= 1
    issues = client.get(f"/jobs/{job['id']}/issues", headers=auth[0]).json()
    assert any(i["title"] == "Exposed secret: AWS access key ID" for i in issues)
