"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { api, getToken, setToken } from "@/lib/api";
import type { Job } from "@/lib/types";

const STATUS_COLOR: Record<string, string> = { completed: "text-pulse-400", failed: "text-red-400", running: "text-pulse-300", queued: "text-ink-400" };

export default function Dashboard() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => api<Job[]>("/jobs").then(setJobs).catch((e) => { if (e.status === 401) { setToken(null); router.replace("/login"); } }), [router]);
  useEffect(() => { if (!getToken()) router.replace("/login"); else load(); }, [load, router]);
  useEffect(() => {
    if (!jobs.some((j) => j.status === "queued" || j.status === "running")) return;
    const t = setInterval(load, 4000); return () => clearInterval(t);
  }, [jobs, load]);

  async function submit(e: FormEvent) {
    e.preventDefault(); setError("");

    const tempId = `pending-${Date.now()}`;
    const name = url.replace(/^https?:\/\/[^/]+\//, "").replace(/\.git\/?$/, "");
    setJobs((js) => [{ id: tempId, status: "queued", progress: 0, queue_reason: "Submitting…", repo_url: url, repo_name: name, private: false,
      trace_id: null, commit_sha: null, error: null, created_at: new Date().toISOString(), finished_at: null, summary: null }, ...js]);
    try {
      const r = await api<{ job_id: string }>("/jobs", { method: "POST", json: { repo_url: url } });
      setUrl(""); router.push(`/jobs/${r.job_id}`);
    } catch (err: any) {
      setJobs((js) => js.filter((j) => j.id !== tempId)); setError(err.message);
    }
  }

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <Nav />
      <form onSubmit={submit} className="mb-8 flex gap-2">
        <label htmlFor="repo" className="sr-only">Repository URL</label>
        <input id="repo" className="flex-1 rounded bg-ink-900 p-3 outline-none ring-1 ring-ink-700 focus:ring-pulse-500" placeholder="https://github.com/owner/repo" value={url} onChange={(e) => setUrl(e.target.value)} required />
        <button className="rounded bg-pulse-500 px-5 font-medium text-ink-950 hover:bg-pulse-400">Analyze</button>
      </form>
      {error && <p role="alert" className="-mt-5 mb-6 text-sm text-red-400">{error}</p>}
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-500">Recent analyses</h2>
      <ul className="divide-y divide-ink-800 rounded-md border border-ink-800">
        {jobs.map((j) => (
          <li key={j.id}>
            <Link href={j.id.startsWith("pending-") ? "#" : `/jobs/${j.id}`} className="flex items-center gap-4 p-4 hover:bg-ink-900">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{j.repo_name}{j.private && <span className="ml-2 text-xs text-ink-500">private</span>}</div>
                <div className="truncate text-xs text-ink-500">{new Date(j.created_at).toLocaleString()}{j.queue_reason && ` · ${j.queue_reason}`}</div>
              </div>
              {j.summary && <span className="text-sm text-ink-400">{j.summary.total_issues} issues{j.summary.degraded && " · degraded"}</span>}
              <span className={`text-sm ${STATUS_COLOR[j.status]}`}>{j.status}{j.status === "running" && ` ${j.progress}%`}</span>
            </Link>
          </li>
        ))}
        {jobs.length === 0 && <li className="p-6 text-center text-sm text-ink-500">No analyses yet. Paste a repository URL above.</li>}
      </ul>
    </main>
  );
}
