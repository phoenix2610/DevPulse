import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ..config import get_settings

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".next", "__pycache__", ".venv", "venv", "target", ".idea"}
LANGS = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".java": "java", ".rb": "ruby", ".php": "php", ".rs": "rust", ".c": "c", ".cpp": "cpp",
    ".cs": "csharp", ".sh": "shell", ".sql": "sql", ".yml": "yaml", ".yaml": "yaml", ".json": "json",
    ".env": "dotenv", ".tf": "terraform", ".md": "markdown", ".html": "html", ".css": "css", ".toml": "toml",
}
_REPO_PATH = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_][\w.-]{0,99}?)(?:\.git)?/?$")


class RepoError(Exception):
    pass


@dataclass
class FileInfo:
    path: str
    size: int
    lines: int
    language: str


@dataclass
class RepoSnapshot:
    root: Path
    commit_sha: str
    files: list[FileInfo] = field(default_factory=list)

    def languages(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.files:
            out[f.language] = out.get(f.language, 0) + f.lines
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def validate_repo_url(url: str) -> tuple[str, str]:
    u = urlparse(url.strip())
    if u.scheme != "https" or (u.hostname or "").lower() not in get_settings().host_allowlist:
        raise RepoError(f"Only https repositories on {sorted(get_settings().host_allowlist)} are supported")
    if u.username or u.password or u.query or u.fragment or not _REPO_PATH.match(u.path):
        raise RepoError("Invalid repository URL; expected https://host/owner/repo")
    if ".." in u.path:
        raise RepoError("Invalid repository URL")
    path = u.path.rstrip("/").removesuffix(".git")
    return f"https://{u.hostname}{path}", path.lstrip("/")


def job_root(job_id: str) -> Path:
    return Path(get_settings().workdir).resolve() / job_id


def clone(url: str, job_id: str, extra_env: dict[str, str] | None = None) -> Path:
    s = get_settings()
    dest = job_root(job_id)
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "https", **(extra_env or {})}
    cmd = ["git", "-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null", "clone", "--depth", "1",
           "--no-tags", "--single-branch", "--", url, str(dest)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=s.clone_timeout_s, env=env)
    except subprocess.TimeoutExpired as e:
        raise RepoError("git clone timed out") from e
    except subprocess.CalledProcessError as e:
        raise RepoError(f"git clone failed: {_scrub(e.stderr.strip()[-300:])}") from e
    return dest


def _scrub(text: str) -> str:
    return re.sub(r"(?i)(authorization:\s*\w+\s+)\S+|x-access-token:[^@\s]+", r"\1***", text)


def _dir_mb(root: Path) -> float:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()) / 1_048_576


def snapshot(root: Path) -> RepoSnapshot:
    s = get_settings()
    if _dir_mb(root) > s.max_repo_mb:
        shutil.rmtree(root, ignore_errors=True)
        raise RepoError(f"Repository exceeds {s.max_repo_mb} MB limit")
    sha = ""
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    snap = RepoSnapshot(root=root, commit_sha=sha)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink())
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink():
                continue
            lang = LANGS.get(p.suffix.lower()) or ("dotenv" if name.startswith(".env") else None)
            if not lang:
                continue
            size = p.stat().st_size
            if size > s.max_file_bytes:
                continue
            text = read_text(p)
            if text is None:
                continue
            snap.files.append(FileInfo(p.relative_to(root).as_posix(), size, text.count("\n") + 1, lang))
    return snap


def read_text(p: Path) -> str | None:
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:2048]:
        return None
    return data.decode("utf-8", errors="replace")


def normalize_rel(rel: str) -> str:
    return "/".join(p for p in rel.replace("\\", "/").split("/") if p not in ("", "."))


def safe_join(root: Path, rel: str) -> Path:
    target = (root / normalize_rel(rel)).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise RepoError("Path outside repository")
    return target
