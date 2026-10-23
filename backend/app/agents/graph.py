import hashlib
import logging
import operator
import re
from dataclasses import dataclass
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .. import sandbox
from ..config import Tier, get_settings
from ..db import SessionLocal
from ..llm.router import ReviewRouter, batches
from ..observability.tracing import span
from . import heuristics, rag, repo

log = logging.getLogger(__name__)

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
Emit = Callable[..., None]


@dataclass
class JobContext:
    job_id: str
    user_id: int
    repo_url: str
    tier: Tier
    git_env: Callable[[], dict[str, str]] = lambda: {}


class ReviewState(TypedDict, total=False):
    snapshot: repo.RepoSnapshot
    security_standards: str
    architecture_standards: str
    issues: Annotated[list[dict], operator.add]
    summary: dict
    final_issues: list[dict]


def fingerprint(issue: dict, line_text: str) -> str:
    key = issue.get("rule_ref") or re.sub(r"\W+", " ", issue["title"].lower()).strip()
    norm = re.sub(r"\s+", " ", line_text).strip()
    return hashlib.sha1(f"{issue['file_path']}|{issue['category']}|{key}|{norm}".encode()).hexdigest()


def _llm_files(snap: repo.RepoSnapshot, limit: int) -> dict[str, str]:
    code = sorted((f for f in snap.files if f.language not in {"markdown", "json"}), key=lambda f: -f.lines)
    return {f.path: repo.read_text(snap.root / f.path) or "" for f in code[:limit]}


def build_graph(ctx: JobContext, emit: Emit, router: ReviewRouter | None = None):
    s = get_settings()
    router = router or ReviewRouter(ctx.job_id, ctx.user_id, ctx.tier, emit)

    def llm_pass(agent: str, prompt: str, standards: str, files: dict[str, str], keep: set[str]) -> list[dict]:
        if not router.enabled:
            emit("No LLM provider configured: static analysis only", agent=agent, level="warn")
            return []
        found: list[dict] = []
        for batch in batches(files):
            out = router.review(agent, prompt, standards, batch)
            if out.issues is None:
                msg = {"job_budget": "Tier token budget for this job reached; remaining files get static analysis only",
                       "throttled": "Rate limit wait exceeded; remaining files get static analysis only",
                       "providers_unavailable": "All LLM providers unavailable; falling back to static analysis",
                       "egress_blocked": "Batch withheld by compliance guard; static analysis only"}.get(out.reason, out.reason)
                emit(msg, agent=agent, level="warn")
                if out.reason in ("job_budget", "throttled"):
                    break
                continue
            found += [{**i, "agent": agent} for i in out.issues if i["category"] in keep]
        return found

    def parse_repo(state: ReviewState) -> ReviewState:
        with span("agent.parse_repo"):
            emit("Validating and cloning repository…", agent="repo-parser", progress=5)
            url, _ = repo.validate_repo_url(ctx.repo_url)
            root = repo.clone(url, ctx.job_id, extra_env=ctx.git_env())
            emit("Parsing repository structure…", agent="repo-parser", progress=20)
            snap = repo.snapshot(root)
            emit(f"Found {len(snap.files)} source files at {snap.commit_sha[:8] or 'unknown'}; languages: "
                 f"{', '.join(f'{k} ({v})' for k, v in list(snap.languages().items())[:5])}", agent="repo-parser", progress=30)
            return {"snapshot": snap}

    def retrieve_standards(state: ReviewState) -> ReviewState:
        with span("agent.retrieve_standards"):
            emit("Querying vector store for OWASP / style standards…", agent="rag", progress=35)
            langs = " ".join(state["snapshot"].languages())
            with SessionLocal() as db:
                rag.seed_standards(db)
                sec = rag.retrieve(db, f"security vulnerabilities injection secrets authentication {langs}", k=6, source="OWASP Top 10")
                arch = rag.retrieve(db, f"architecture layering function size error handling tests {langs}", k=4, source="Style Guide")
                fmt = lambda rows: "\n".join(f"- {r.title}: {r.content}" for r in rows)
                out = {"security_standards": fmt(sec), "architecture_standards": fmt(arch)}
            emit(f"Retrieved {len(sec)} security and {len(arch)} style standards", agent="rag", progress=40)
            return out

    def security_review(state: ReviewState) -> ReviewState:
        with span("agent.security_review"):
            snap = state["snapshot"]
            emit("Scanning for exposed keys and vulnerabilities (static + AST)…", agent="security-reviewer", progress=50)
            found = heuristics.security_scan(snap)
            if sandbox.available():
                try:
                    sb = sandbox.python_syntax_check(snap.root)
                    found += sb
                    emit(f"Sandbox syntax check done ({len(sb)} errors)", agent="sandbox")
                except Exception as e:
                    emit(f"Sandbox check skipped: {e}", agent="sandbox", level="warn")
            found += llm_pass("security-reviewer", "security_reviewer", state["security_standards"],
                              _llm_files(snap, s.max_files_for_llm), {"security", "bug"})
            emit(f"Security review found {len(found)} issues", agent="security-reviewer", progress=70)
            return {"issues": found}

    def architecture_review(state: ReviewState) -> ReviewState:
        with span("agent.architecture_review"):
            snap = state["snapshot"]
            emit("Evaluating architectural patterns…", agent="architecture-reviewer", progress=50)
            found = heuristics.architecture_scan(snap)
            found += llm_pass("architecture-reviewer", "architecture_reviewer", state["architecture_standards"],
                              _llm_files(snap, min(s.max_files_for_llm, 25)), {"architecture", "style"})
            emit(f"Architecture review found {len(found)} issues", agent="architecture-reviewer", progress=75)
            return {"issues": found}

    def consolidate(state: ReviewState) -> ReviewState:
        emit("Consolidating findings…", agent="orchestrator", progress=85)
        snap = state["snapshot"]
        lines_cache: dict[str, list[str]] = {}
        seen, merged = set(), []
        for i in sorted(state.get("issues", []), key=lambda i: (SEV_ORDER[i["severity"]], i["file_path"], i["line_start"])):
            if i["file_path"] not in lines_cache:
                lines_cache[i["file_path"]] = (repo.read_text(snap.root / i["file_path"]) or "").split("\n") \
                    if (snap.root / i["file_path"]).is_file() else []
            lines = lines_cache[i["file_path"]]
            text = lines[i["line_start"] - 1] if 0 < i["line_start"] <= len(lines) else ""
            fp = fingerprint(i, text)
            if fp in seen:
                continue
            seen.add(fp)
            merged.append({**i, "fingerprint": fp, "source": i.get("source", "static")})
        by_sev: dict[str, int] = {}
        for i in merged:
            by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1
        st = router.stats
        llm_ok = sum(v for k, v in st.items() if k.startswith("provider:"))
        degraded = router.enabled and any(st[k] for k in ("providers_unavailable", "throttled", "job_budget", "egress_blocked"))
        summary = {
            "total_issues": len(merged), "by_severity": by_sev, "files_scanned": len(snap.files),
            "kloc": round(sum(f.lines for f in snap.files if f.language not in {"markdown", "json"}) / 1000, 2),
            "languages": snap.languages(), "commit_sha": snap.commit_sha,
            "llm_used": llm_ok > 0, "degraded": bool(degraded or not router.enabled),
            "providers": {k.split(":", 1)[1]: v for k, v in st.items() if k.startswith("provider:")},
            "failovers": st["failovers"], "throttled_batches": st["throttled"], "budget_capped": st["job_budget"] > 0,
            "redactions": router.sanitizer.report(),
        }
        return {"summary": summary, "final_issues": merged}

    g = StateGraph(ReviewState)
    for name, fn in [("parse_repo", parse_repo), ("retrieve_standards", retrieve_standards),
                     ("security_review", security_review), ("architecture_review", architecture_review),
                     ("consolidate", consolidate)]:
        g.add_node(name, fn)
    g.add_edge(START, "parse_repo")
    g.add_edge("parse_repo", "retrieve_standards")
    g.add_edge("retrieve_standards", "security_review")
    g.add_edge("retrieve_standards", "architecture_review")
    g.add_edge(["security_review", "architecture_review"], "consolidate")
    g.add_edge("consolidate", END)
    return g.compile()
