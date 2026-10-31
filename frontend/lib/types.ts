export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface JobSummary {
  total_issues: number; by_severity: Record<string, number>; files_scanned: number; kloc: number;
  languages: Record<string, number>; llm_used: boolean; degraded: boolean; providers: Record<string, number>;
  failovers: number; throttled_batches: number; budget_capped: boolean; redactions: Record<string, number>;
  lifecycle?: { new: number; reopened: number; resolved: number }; duration_s?: number;
}
export interface Job {
  id: string; status: JobStatus; progress: number; queue_reason: string | null; repo_url: string; repo_name: string;
  private: boolean; trace_id: string | null; commit_sha: string | null; error: string | null; created_at: string;
  finished_at: string | null; summary: JobSummary | null;
}
export interface Issue {
  id: number; file_path: string; line_start: number; line_end: number; severity: Severity; category: string;
  agent: string; title: string; description: string; suggestion: string | null; rule_ref: string | null;
  source: string; prompt_version: string | null; my_vote: 1 | -1 | null; finding_id: number | null;
  finding_status: "open" | "resolved" | "dismissed" | null;
}
export interface TreeEntry { name: string; path: string; type: "dir" | "file"; size: number; issue_count: number }
export interface FileData { path: string; language: string; content: string; issues: Issue[] }
export interface LogEvent { seq: number; agent: string; level: string; message: string; progress: number | null; ts: string }
export interface Me { id: number; email: string; tier: string; is_admin: boolean; github_login: string | null;
  limits: { llm_tokens_per_minute: number; max_llm_tokens_per_job: number; max_concurrent_jobs: number; max_throttle_wait_s: number } }

export interface Forecast { slope_per_week: number; r2: number; projected: number; horizon_days: number; trend: string; line: { t: string; value: number }[] }
export interface RepoHealth {
  repository_id: number; name: string; health_score: number; grade: string; open_by_severity: Record<string, number>;
  kloc: number; scans: number; last_scan: string; debt_series: { t: string; value: number }[]; forecast: Forecast | null;
}
export interface Overview {
  window_days: number; scans: number; open_findings: Record<string, number>; avg_health_score: number | null;
  timeseries: ({ t: string; job_id: string; repo: string; total_issues: number } & Record<Severity, number>)[];
  repos: RepoHealth[];
  mttr: { avg_hours: number | null; resolved: number; by_severity: { severity: Severity; avg_hours: number; count: number }[] };
  llm: { total_cost_usd: number; total_tokens: number; calls: number; latency_p50_ms: number | null; latency_p95_ms: number | null;
    by_provider: Record<string, Record<string, number>>; failovers: number; degraded_jobs: number; redactions: number;
    daily: { date: string; cost_usd: number; tokens: number; calls: number; errors: number }[] };
  prompts: { prompt_version: string; issues: number; up: number; down: number; approval: number | null }[];
}

export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export const SEV_STYLE: Record<Severity, string> = {
  critical: "bg-red-500/20 text-red-300 border-red-500/40", high: "bg-orange-500/20 text-orange-300 border-orange-500/40",
  medium: "bg-yellow-500/20 text-yellow-300 border-yellow-500/40", low: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  info: "bg-ink-500/20 text-ink-300 border-ink-500/40",
};

export const SEV_RAMP: Record<Exclude<Severity, "info">, string> = {
  critical: "#cde2fb", high: "#86b6ef", medium: "#3987e5", low: "#1c5cab",
};
