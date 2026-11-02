"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import CodeViewer from "@/components/CodeViewer";
import FileTree from "@/components/FileTree";
import IssueList from "@/components/IssueList";
import LogStream from "@/components/LogStream";
import { api, getToken } from "@/lib/api";
import { useJobStream } from "@/lib/useJobStream";
import type { FileData, Issue, Job, JobStatus } from "@/lib/types";

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [file, setFile] = useState<FileData | null>(null);
  const [focusLine, setFocusLine] = useState<number | null>(null);
  const [activeIssue, setActiveIssue] = useState<number | null>(null);
  const [tab, setTab] = useState<"logs" | "issues">("logs");
  const [error, setError] = useState("");

  const loadJob = useCallback(async () => {
    const j = await api<Job>(`/jobs/${id}`);
    setJob(j);
    if (j.status === "completed") { setIssues(await api<Issue[]>(`/jobs/${id}/issues`)); setTab("issues"); }
    return j;
  }, [id]);

  const onDone = useCallback((_: JobStatus) => { loadJob(); }, [loadJob]);
  const { logs, progress, connected } = useJobStream(id, onDone);

  useEffect(() => { if (!getToken()) router.replace("/login"); else loadJob().catch((e) => setError(e.message)); }, [loadJob, router]);

  const openFile = useCallback(async (path: string, line: number | null = null, issueId: number | null = null) => {
    try {
      setFile(await api<FileData>(`/jobs/${id}/file?path=${encodeURIComponent(path)}`));
      setFocusLine(line); setActiveIssue(issueId);
    } catch (e: any) { setError(e.message); }
  }, [id]);


  const patch = useCallback((match: (i: Issue) => boolean, fields: Partial<Issue>) => {
    const upd = (xs: Issue[]) => xs.map((i) => (match(i) ? { ...i, ...fields } : i));
    setIssues(upd);
    setFile((f) => (f ? { ...f, issues: upd(f.issues) } : f));
  }, []);


  const onVote = useCallback(async (issue: Issue, vote: 1 | -1 | null) => {
    const prev = issue.my_vote;
    patch((i) => i.id === issue.id, { my_vote: vote });
    try {
      if (vote === null) await api(`/issues/${issue.id}/feedback`, { method: "DELETE" });
      else await api(`/issues/${issue.id}/feedback`, { method: "PUT", json: { vote } });
    } catch (e: any) { patch((i) => i.id === issue.id, { my_vote: prev }); setError(`Feedback not saved: ${e.message}`); }
  }, [patch]);

  const onFindingStatus = useCallback(async (issue: Issue, status: "open" | "dismissed") => {
    const prev = issue.finding_status;
    patch((i) => i.finding_id === issue.finding_id, { finding_status: status });
    try { await api(`/findings/${issue.finding_id}`, { method: "PATCH", json: { status } }); }
    catch (e: any) { patch((i) => i.finding_id === issue.finding_id, { finding_status: prev }); setError(`Not saved: ${e.message}`); }
  }, [patch]);

  const onPick = useCallback((i: Issue) => { setActiveIssue(i.id); openFile(i.file_path, i.line_start, i.id); }, [openFile]);

  if (error && !job) return <main className="p-8 text-red-400">{error}</main>;
  const status = job?.status === "queued" && progress > 0 ? "running" : job?.status ?? "queued";
  const pct = status === "completed" ? 100 : Math.max(progress, job?.progress ?? 0);
  const s = job?.summary;
  const redactions = s ? Object.values(s.redactions ?? {}).reduce((a, b) => a + b, 0) : 0;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center gap-4 border-b border-ink-800 px-4 py-2">
        <Link href="/" className="text-ink-400 hover:text-white" aria-label="Back to analyses">←</Link>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 truncate font-semibold">{job?.repo_name ?? "…"}
            {job?.private && <span className="rounded bg-ink-800 px-1.5 text-[10px] font-normal text-ink-300">private · scoped token</span>}</div>
          <div className="text-xs text-ink-500">{status}{s && ` · ${s.files_scanned} files · ${s.total_issues} issues`}{job?.commit_sha && ` · ${job.commit_sha.slice(0, 8)}`}
            {job?.trace_id && <span title="OpenTelemetry trace id"> · trace {job.trace_id.slice(0, 12)}…</span>}</div>
        </div>
        {s && (
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {s.llm_used && <Badge tone="pulse">AI: {Object.keys(s.providers).join(", ")}</Badge>}
            {s.failovers > 0 && <Badge tone="amber">{s.failovers} failover{s.failovers > 1 ? "s" : ""}</Badge>}
            {s.degraded && <Badge tone="amber">degraded · static fallback</Badge>}
            {redactions > 0 && <Badge tone="ink">{redactions} values masked before LLM</Badge>}
          </div>
        )}
        {(status === "running" || status === "queued") && (
          <div className="w-52">
            <div className="h-2 overflow-hidden rounded bg-ink-800" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full bg-pulse-500 transition-all" style={{ width: `${pct}%` }} /></div>
            <div className="mt-0.5 truncate text-right text-xs text-ink-500">{job?.queue_reason ?? `${pct}%`} {connected ? "● live" : "○ reconnecting"}</div>
          </div>
        )}
        {status === "failed" && <span className="text-sm text-red-400">{job?.error}</span>}
      </header>
      {error && job && <div role="alert" className="flex justify-between bg-red-500/10 px-4 py-1 text-xs text-red-300">{error}<button onClick={() => setError("")}>✕</button></div>}
      <div className="grid min-h-0 flex-1 grid-cols-[280px_1fr_380px]">
        <aside className="min-h-0 border-r border-ink-800 py-1">
          <FileTree jobId={id} selected={file?.path ?? null} onSelect={(p) => openFile(p)} refreshKey={status} />
        </aside>
        <section className="min-w-0 border-r border-ink-800">
          {file ? (
            <div className="flex h-full flex-col">
              <div className="border-b border-ink-800 px-3 py-1.5 font-mono text-xs text-ink-400">{file.path}</div>
              <div className="min-h-0 flex-1"><CodeViewer file={file} focusLine={focusLine} onPinClick={(iid) => { setActiveIssue(iid); setTab("issues"); }} /></div>
            </div>
          ) : <div className="grid h-full place-items-center text-sm text-ink-500">Select a file to view annotated findings</div>}
        </section>
        <aside className="flex min-h-0 flex-col">
          <div className="flex border-b border-ink-800 text-sm" role="tablist">
            {(["logs", "issues"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                className={`flex-1 py-2 capitalize ${tab === t ? "border-b-2 border-pulse-500 text-white" : "text-ink-400"}`}>{t}{t === "issues" && ` (${issues.length})`}</button>
            ))}
          </div>
          <div className="min-h-0 flex-1">
            {tab === "logs" ? <LogStream logs={logs} /> :
              <IssueList issues={issues} activeId={activeIssue} onPick={onPick} onVote={onVote} onFindingStatus={onFindingStatus} />}
          </div>
        </aside>
      </div>
    </div>
  );
}

function Badge({ tone, children }: { tone: "pulse" | "amber" | "ink"; children: React.ReactNode }) {
  const c = { pulse: "border border-pulse-500/40 text-pulse-300", amber: "border border-amber-500/40 text-amber-300", ink: "border border-ink-600 text-ink-300" }[tone];
  return <span className={`rounded-sm px-1.5 py-0.5 ${c}`}>{children}</span>;
}
