"use client";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Wordmark } from "@/components/Nav";
import { api, setToken } from "@/lib/api";

const STAGES = [
  ["parse", "Clone at depth 1 and inventory every source file"],
  ["retrieve", "Pull matching OWASP and style standards from the vector store"],
  ["review", "Security and architecture agents run in parallel: AST, Semgrep, then the LLM"],
  ["mask", "Secrets and PII are redacted before any byte leaves for a model"],
  ["report", "Findings land on the exact line, tracked across every later scan"],
] as const;

export default function Login() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const r = await api<{ access_token: string }>(`/auth/${mode}`, { method: "POST", json: { email, password } });
      setToken(r.access_token); router.push("/");
    } catch (err: any) { setError(err.message); } finally { setBusy(false); }
  }

  const field = "w-full rounded border border-ink-700 bg-ink-900 px-3 py-2 text-sm outline-none placeholder:text-ink-500 focus:border-pulse-500";
  return (
    <main className="mx-auto grid min-h-screen max-w-5xl items-center gap-12 px-6 py-12 md:grid-cols-[1.3fr_1fr]">
      <section>
        <Wordmark className="text-lg" />
        <h1 className="mt-6 max-w-md text-2xl font-semibold leading-snug text-ink-100">
          Point it at a repository. Get findings pinned to the line that caused them.
        </h1>
        <ol className="mt-8 space-y-3 border-l border-ink-800 pl-4">
          {STAGES.map(([k, v]) => (
            <li key={k} className="grid grid-cols-[72px_1fr] gap-3 text-sm">
              <span className="font-mono text-xs leading-5 text-pulse-400">{k}</span>
              <span className="text-ink-400">{v}</span>
            </li>
          ))}
        </ol>
      </section>
      <section className="rounded-md border border-ink-800 bg-ink-900/60 p-6">
        <h2 className="mb-4 text-sm font-medium text-ink-200">{mode === "login" ? "Sign in" : "Create an account"}</h2>
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-xs text-ink-400">Email
            <input className={`${field} mt-1`} type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label className="block text-xs text-ink-400">Password <span className="text-ink-600">(8+ characters)</span>
            <input className={`${field} mt-1`} type="password" required minLength={8} autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
          <button disabled={busy} className="w-full rounded bg-pulse-500 py-2 text-sm font-medium text-ink-950 hover:bg-pulse-400 disabled:opacity-50">
            {mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <button className="mt-4 text-xs text-ink-400 hover:text-white" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "No account yet? Register" : "Already registered? Sign in"}
        </button>
      </section>
    </main>
  );
}
