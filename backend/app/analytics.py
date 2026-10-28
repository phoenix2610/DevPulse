import math
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AnalysisJob, CodeIssue, Finding, IssueFeedback, LLMCall, Repository, aware, utcnow

SEV_WEIGHT = {"critical": 10.0, "high": 5.0, "medium": 2.0, "low": 0.5, "info": 0.0}
SEVERITIES = list(SEV_WEIGHT)


def debt_points(by_sev: dict[str, int]) -> float:
    return round(sum(SEV_WEIGHT.get(s, 0) * n for s, n in by_sev.items()), 1)


def health_score(by_sev: dict[str, int], kloc: float) -> float:
    density = debt_points(by_sev) / max(kloc, 1.0)
    return round(100 * math.exp(-density / 25), 1)


def grade(score: float) -> str:
    return next(g for cut, g in ((90, "A"), (75, "B"), (60, "C"), (40, "D"), (-1, "F")) if score >= cut)


def linear_forecast(points: list[tuple[datetime, float]], horizon_days: int = 30) -> dict | None:
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    ys = [v for _, v in points]
    n, mx, my = len(xs), sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys)) / ss_tot if ss_tot else 1.0
    end_x = xs[-1] + horizon_days
    projected = max(0.0, intercept + slope * end_x)
    trend = "rising" if slope * 7 > 0.5 else "falling" if slope * 7 < -0.5 else "stable"
    return {"slope_per_week": round(slope * 7, 2), "r2": round(r2, 2), "projected": round(projected, 1),
            "horizon_days": horizon_days, "trend": trend,
            "line": [{"t": points[-1][0].isoformat(), "value": round(intercept + slope * xs[-1], 1)},
                     {"t": (t0 + timedelta(days=end_x)).isoformat(), "value": round(projected, 1)}]}


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def overview(db: Session, user_id: int, days: int = 90) -> dict:
    since = utcnow() - timedelta(days=days)
    jobs = [j for j in db.scalars(select(AnalysisJob).where(AnalysisJob.user_id == user_id, AnalysisJob.status == "completed")
                                  .order_by(AnalysisJob.finished_at)) if aware(j.finished_at) >= since]
    job_ids = [j.id for j in jobs]
    counts: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    if job_ids:
        for jid, cat, sev, n in db.execute(select(CodeIssue.job_id, CodeIssue.category, CodeIssue.severity, func.count())
                                           .where(CodeIssue.job_id.in_(job_ids))
                                           .group_by(CodeIssue.job_id, CodeIssue.category, CodeIssue.severity)):
            counts[jid][cat][sev] += n

    timeseries = []
    per_repo: dict[int, list] = defaultdict(list)
    for j in jobs:
        sec = counts[j.id].get("security", {})
        all_sev: dict[str, int] = defaultdict(int)
        for cat in counts[j.id].values():
            for s, n in cat.items():
                all_sev[s] += n
        timeseries.append({"t": aware(j.finished_at).isoformat(), "job_id": j.id, "repo": j.repository.name,
                           **{s: sec.get(s, 0) for s in SEVERITIES}, "total_issues": sum(all_sev.values())})
        per_repo[j.repository_id].append((j, dict(all_sev)))

    repos = []
    open_rows = db.execute(select(Finding.repository_id, Finding.severity, func.count()).join(Repository)
                           .where(Repository.user_id == user_id, Finding.status == "open")
                           .group_by(Finding.repository_id, Finding.severity)).all()
    open_by_repo: dict[int, dict[str, int]] = defaultdict(dict)
    for rid, sev, n in open_rows:
        open_by_repo[rid][sev] = n
    for rid, series in per_repo.items():
        latest_job, latest_sev = series[-1]
        kloc = float((latest_job.summary or {}).get("kloc") or 0)
        by_sev = open_by_repo.get(rid) or latest_sev
        score = health_score(by_sev, kloc)
        debt = [(aware(j.finished_at), debt_points(sev)) for j, sev in series]
        repos.append({"repository_id": rid, "name": latest_job.repository.name, "health_score": score, "grade": grade(score),
                      "open_by_severity": by_sev, "kloc": kloc, "scans": len(series),
                      "last_scan": aware(latest_job.finished_at).isoformat(),
                      "debt_series": [{"t": t.isoformat(), "value": v} for t, v in debt],
                      "forecast": linear_forecast(debt)})
    repos.sort(key=lambda r: r["health_score"])

    resolved = db.execute(select(Finding.severity, Finding.first_seen_at, Finding.resolved_at).join(Repository)
                          .where(Repository.user_id == user_id, Finding.status == "resolved")).all()
    hours_by_sev: dict[str, list[float]] = defaultdict(list)
    for sev, first, done in resolved:
        hours_by_sev[sev].append((aware(done) - aware(first)).total_seconds() / 3600)
    all_hours = [h for hs in hours_by_sev.values() for h in hs]
    mttr = {"avg_hours": round(sum(all_hours) / len(all_hours), 1) if all_hours else None, "resolved": len(all_hours),
            "by_severity": [{"severity": s, "avg_hours": round(sum(h) / len(h), 1), "count": len(h)}
                            for s in SEVERITIES if (h := hours_by_sev.get(s))]}

    calls = [c for c in db.scalars(select(LLMCall).where(LLMCall.user_id == user_id)) if aware(c.created_at) >= since]
    daily: dict[str, dict] = defaultdict(lambda: {"cost_usd": 0.0, "tokens": 0, "calls": 0, "errors": 0})
    lat_ok, by_provider = [], defaultdict(lambda: defaultdict(int))
    for c in calls:
        d = daily[aware(c.created_at).date().isoformat()]
        d["cost_usd"] += c.cost_usd
        d["tokens"] += c.input_tokens + c.output_tokens
        d["calls"] += 1
        d["errors"] += c.status != "ok"
        by_provider[c.provider][c.status] += 1
        if c.status == "ok":
            lat_ok.append(c.latency_ms)
    llm = {"total_cost_usd": round(sum(c.cost_usd for c in calls), 4),
           "total_tokens": sum(c.input_tokens + c.output_tokens for c in calls), "calls": len(calls),
           "latency_p50_ms": percentile(lat_ok, 0.5), "latency_p95_ms": percentile(lat_ok, 0.95),
           "by_provider": {p: dict(v) for p, v in by_provider.items()},
           "failovers": sum((j.summary or {}).get("failovers", 0) for j in jobs),
           "degraded_jobs": sum(bool((j.summary or {}).get("degraded")) for j in jobs),
           "redactions": sum(sum((j.summary or {}).get("redactions", {}).values()) for j in jobs),
           "daily": [{"date": k, **{**v, "cost_usd": round(v["cost_usd"], 4)}} for k, v in sorted(daily.items())]}

    prompt_rows = []
    if job_ids:
        issued = dict(db.execute(select(func.coalesce(CodeIssue.prompt_version, CodeIssue.source), func.count())
                                 .where(CodeIssue.job_id.in_(job_ids))
                                 .group_by(func.coalesce(CodeIssue.prompt_version, CodeIssue.source))).all())
        votes: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for ver, src, vote in db.execute(select(IssueFeedback.prompt_version, IssueFeedback.source, IssueFeedback.vote)
                                         .where(IssueFeedback.user_id == user_id)):
            votes[ver or src][0 if vote > 0 else 1] += 1
        for key in sorted(set(issued) | set(votes)):
            up, down = votes[key]
            prompt_rows.append({"prompt_version": key, "issues": issued.get(key, 0), "up": up, "down": down,
                                "approval": round(up / (up + down), 2) if up + down else None})

    open_total: dict[str, int] = defaultdict(int)
    for v in open_by_repo.values():
        for s, n in v.items():
            open_total[s] += n
    return {"window_days": days, "scans": len(jobs), "open_findings": dict(open_total),
            "avg_health_score": round(sum(r["health_score"] for r in repos) / len(repos), 1) if repos else None,
            "timeseries": timeseries, "repos": repos, "mttr": mttr, "llm": llm, "prompts": prompt_rows}
