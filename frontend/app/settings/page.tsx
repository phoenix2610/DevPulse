"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { api, getToken } from "@/lib/api";
import type { Me } from "@/lib/types";

interface GhStatus { enabled: boolean; connected: boolean; login: string | null; install_url: string | null }
interface GrantedRepo { full_name: string; private: boolean; html_url: string; installation_id: number }

const NOTICES: Record<string, string> = { connected: "GitHub connected.", invalid_state: "That authorization link expired or was already used. Try again.", error: "GitHub authorization failed." };

function Settings() {
  const router = useRouter();
  const notice = useSearchParams().get("github");
  const [me, setMe] = useState<Me | null>(null);
  const [gh, setGh] = useState<GhStatus | null>(null);
  const [repos, setRepos] = useState<GrantedRepo[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setMe(await api<Me>("/auth/me"));
    const s = await api<GhStatus>("/integrations/github/status");
    setGh(s);
    if (s.connected) setRepos(await api<GrantedRepo[]>("/integrations/github/repos").catch(() => []));
  }, []);
  useEffect(() => { if (!getToken()) router.replace("/login"); else load().catch((e) => setError(e.message)); }, [load, router]);

  const connect = async () => { const r = await api<{ url: string }>("/integrations/github/authorize", { method: "POST" }); window.location.href = r.url; };
  const disconnect = async () => {
    const prev = gh;
    setGh((g) => g && { ...g, connected: false, login: null }); setRepos(null);
    try { await api("/integrations/github", { method: "DELETE" }); } catch (e: any) { setGh(prev); setError(e.message); }
  };
  const analyze = async (full: string) => {
    try { const r = await api<{ job_id: string }>("/jobs", { method: "POST", json: { repo_url: `https://github.com/${full}` } }); router.push(`/jobs/${r.job_id}`); }
    catch (e: any) { setError(e.message); }
  };

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <Nav />
      {notice && NOTICES[notice] && <p role="status" className="mb-4 rounded bg-ink-800 p-3 text-sm">{NOTICES[notice]}</p>}
      {error && <p role="alert" className="mb-4 text-sm text-red-400">{error}</p>}
      <section className="mb-8 rounded-md border border-ink-800 p-5">
        <h2 className="mb-1 font-semibold">Plan</h2>
        {me && <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
          <div><dt className="text-ink-500">Tier</dt><dd className="capitalize">{me.tier}</dd></div>
          <div><dt className="text-ink-500">LLM tokens / min</dt><dd>{me.limits.llm_tokens_per_minute.toLocaleString()}</dd></div>
          <div><dt className="text-ink-500">Tokens / job</dt><dd>{me.limits.max_llm_tokens_per_job.toLocaleString()}</dd></div>
          <div><dt className="text-ink-500">Concurrent jobs</dt><dd>{me.limits.max_concurrent_jobs}</dd></div>
        </dl>}
        <p className="mt-3 text-xs text-ink-500">Over the limit, scans queue or throttle and finish with static analysis; they are never rejected.</p>
      </section>
      <section className="rounded-md border border-ink-800 p-5">
        <h2 className="mb-1 font-semibold">GitHub (least privilege)</h2>
        <p className="mb-4 text-sm text-ink-400">The reviewer is a GitHub App: you choose exactly which repositories it can read. Each scan uses a
          one-hour, single-repository, read-only token that is never stored.</p>
        {gh && !gh.enabled && <p className="text-sm text-ink-500">GitHub App not configured on this server (public repositories still work).</p>}
        {gh?.enabled && !gh.connected && <button onClick={connect} className="rounded bg-pulse-500 px-4 py-2 text-sm font-medium text-ink-950 hover:bg-pulse-400">Connect GitHub</button>}
        {gh?.connected && (
          <>
            <div className="mb-4 flex items-center gap-3 text-sm">Connected as <b>{gh.login}</b>
              {gh.install_url && <a href={gh.install_url} target="_blank" rel="noreferrer" className="text-pulse-400 underline-offset-2 hover:underline">Choose repositories ↗</a>}
              <button onClick={disconnect} className="ml-auto text-ink-400 hover:text-red-400">Disconnect &amp; revoke</button></div>
            <ul className="divide-y divide-ink-800 rounded border border-ink-800">
              {repos === null && <li className="p-3 text-sm text-ink-500">Loading granted repositories…</li>}
              {repos?.length === 0 && <li className="p-3 text-sm text-ink-500">No repositories granted yet. Use “Choose repositories”.</li>}
              {repos?.map((r) => (
                <li key={r.full_name} className="flex items-center gap-3 p-3 text-sm">
                  <span className="flex-1 truncate">{r.full_name}</span>{r.private && <span className="text-xs text-ink-500">private</span>}
                  <button onClick={() => analyze(r.full_name)} className="rounded bg-ink-800 px-3 py-1 hover:bg-ink-700">Analyze</button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </main>
  );
}

export default function SettingsPage() {
  return <Suspense><Settings /></Suspense>;
}
