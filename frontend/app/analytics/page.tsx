"use client";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import Nav from "@/components/Nav";
import { api, getToken } from "@/lib/api";
import { Overview, SEV_RAMP } from "@/lib/types";


const GRID = "#1f2522", AXIS = "#95a09a", SERIES = "#3987e5";
const STACK = ["low", "medium", "high", "critical"] as const;
const tick = { fill: AXIS, fontSize: 11 };
const day = (t: string | number) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const stamp = (t: string | number) => new Date(t).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

const tickFor = (ts: number[]) => (ts.length && Math.max(...ts) - Math.min(...ts) < 2 * 86400e3
  ? (t: number) => new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : day);

const legendText = (v: string) => <span style={{ color: AXIS }}>{v}</span>;
const tooltipStyle = { contentStyle: { background: "#141816", border: "1px solid #2c3430", borderRadius: 3, fontSize: 12 }, labelStyle: { color: "#dce2de" } };

function fmtHours(h: number | null) {
  if (h == null) return "—";
  return h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} d`;
}

export default function AnalyticsPage() {
  const router = useRouter();
  const [days, setDays] = useState(90);
  const [data, setData] = useState<Overview | null>(null);
  const [repoId, setRepoId] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) return router.replace("/login");
    api<Overview>(`/analytics/overview?days=${days}`).then((d) => {
      setData(d);
      setRepoId((cur) => cur ?? [...d.repos].sort((a, b) => b.last_scan.localeCompare(a.last_scan))[0]?.repository_id ?? null);
    }).catch((e) => setError(e.message));
  }, [days, router]);

  const repo = data?.repos.find((r) => r.repository_id === repoId) ?? null;
  const debt = useMemo(() => {
    if (!repo) return [];
    const pts: { t: number; actual?: number; forecast?: number }[] = repo.debt_series.map((p) => ({ t: Date.parse(p.t), actual: p.value }));
    repo.forecast?.line.forEach((p, i) => (i === 0 ? (pts[pts.length - 1].forecast = p.value) : pts.push({ t: Date.parse(p.t), forecast: p.value })));
    return pts;
  }, [repo]);

  const vulns = useMemo(() => (data?.timeseries ?? []).map((r) => ({ ...r, ts: Date.parse(r.t) })), [data]);
  const vulnTick = useMemo(() => tickFor(vulns.map((v) => v.ts)), [vulns]);
  const debtTick = useMemo(() => tickFor(debt.map((d) => d.t)), [debt]);
  const openTotal = data ? Object.values(data.open_findings).reduce((a, b) => a + b, 0) : 0;

  return (
    <main className="mx-auto max-w-6xl px-4 py-8">
      <Nav />
      <div className="mb-6 flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-xl font-semibold">Engineering health</h2>
        {[30, 90, 365].map((d) => (
          <button key={d} onClick={() => setDays(d)} aria-pressed={days === d}
            className={`rounded px-3 py-1 text-sm ${days === d ? "bg-ink-700 text-white" : "bg-ink-900 text-ink-400 hover:text-white"}`}>Last {d} days</button>
        ))}
      </div>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      {data && data.scans === 0 && <p className="rounded-md border border-ink-800 p-8 text-center text-ink-400">No completed scans in this window yet.</p>}
      {data && data.scans > 0 && (
        <>
          <section className="mb-6 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-ink-800 bg-ink-800 md:grid-cols-3 lg:grid-cols-6">
            <Stat label="Avg health score" value={data.avg_health_score?.toFixed(0) ?? "—"} sub="0 to 100, weighted by severity per KLOC" />
            <Stat label="Open findings" value={openTotal.toLocaleString()} sub={`${data.open_findings.critical ?? 0} critical · ${data.open_findings.high ?? 0} high`} />
            <Stat label="Mean time to resolve" value={fmtHours(data.mttr.avg_hours)} sub={`${data.mttr.resolved} resolved by a later scan`} />
            <Stat label="LLM spend" value={`$${data.llm.total_cost_usd.toFixed(2)}`} sub={`${data.llm.total_tokens.toLocaleString()} tokens · ${data.llm.calls} calls`} />
            <Stat label="LLM latency p50 / p95" value={data.llm.latency_p50_ms != null ? `${(data.llm.latency_p50_ms / 1000).toFixed(1)}s` : "—"}
              sub={data.llm.latency_p95_ms != null ? `p95 ${(data.llm.latency_p95_ms / 1000).toFixed(1)}s` : "no successful calls"} />
            <Stat label="Values masked" value={data.llm.redactions.toLocaleString()} sub={`${data.llm.failovers} failovers · ${data.llm.degraded_jobs} degraded scans`} />
          </section>

          <Card title="Security vulnerabilities over time" subtitle="Security findings per completed scan, stacked by severity"
            action={<button onClick={() => setTable((t) => !t)} className="text-xs text-ink-400 hover:text-white">{table ? "Show chart" : "Show table"}</button>}>
            {table ? (
              <div className="max-h-72 overflow-auto"><table className="w-full text-sm tabular-nums">
                <thead className="text-left text-xs text-ink-500"><tr><th className="py-1">Scan</th><th>Repository</th>{STACK.slice().reverse().map((s) => <th key={s} className="text-right capitalize">{s}</th>)}</tr></thead>
                <tbody>{data.timeseries.map((r) => (
                  <tr key={r.job_id} className="border-t border-ink-800"><td className="py-1">{day(r.t)}</td><td>{r.repo}</td>
                    {STACK.slice().reverse().map((s) => <td key={s} className="text-right">{r[s]}</td>)}</tr>))}</tbody>
              </table></div>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={vulns} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={vulnTick} tick={tick} axisLine={{ stroke: GRID }} tickLine={false} minTickGap={32} />
                  <YAxis allowDecimals={false} tick={tick} axisLine={false} tickLine={false} />
                  <Tooltip {...tooltipStyle} labelFormatter={(t, p) => `${stamp(t as number)} · ${p?.[0]?.payload?.repo ?? ""}`} cursor={{ stroke: AXIS, strokeDasharray: "3 3" }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} formatter={legendText} />
                  {STACK.map((s) => (
                    <Area key={s} type="monotone" dataKey={s} name={s[0].toUpperCase() + s.slice(1)} stackId="sev" stroke={SEV_RAMP[s]}
                      strokeWidth={2} fill={SEV_RAMP[s]} fillOpacity={0.35} isAnimationActive={false} />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            )}
          </Card>

          <div className="grid gap-x-6 lg:grid-cols-2">
            <Card title="Tech debt trend & 30-day forecast" subtitle={repo?.forecast
              ? `Debt points (severity-weighted findings). Trend ${repo.forecast.trend}, ${repo.forecast.slope_per_week >= 0 ? "+" : ""}${repo.forecast.slope_per_week}/week, R² ${repo.forecast.r2}`
              : "Debt points per scan. Forecast appears after 3 scans."}
              action={data.repos.length > 1 && (
                <select aria-label="Repository" value={repoId ?? ""} onChange={(e) => setRepoId(Number(e.target.value))}
                  className="rounded bg-ink-900 px-2 py-1 text-xs ring-1 ring-ink-700">
                  {data.repos.map((r) => <option key={r.repository_id} value={r.repository_id}>{r.name}</option>)}
                </select>)}>
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={debt} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
                  <CartesianGrid stroke={GRID} vertical={false} />
                  <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={debtTick} tick={tick} axisLine={{ stroke: GRID }} tickLine={false} minTickGap={32} />
                  <YAxis tick={tick} axisLine={false} tickLine={false} />
                  <Tooltip {...tooltipStyle} labelFormatter={(t) => stamp(t as number)} cursor={{ stroke: AXIS, strokeDasharray: "3 3" }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} formatter={legendText} />
                  <Line dataKey="actual" name="Actual" stroke={SERIES} strokeWidth={2} dot={{ r: 4, fill: SERIES, stroke: "#0b0d0c", strokeWidth: 2 }} isAnimationActive={false} connectNulls />
                  <Line dataKey="forecast" name="Forecast" stroke={SERIES} strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </Card>

            <Card title="Mean time to resolve, by severity" subtitle="Hours from first detection until a scan no longer finds it">
              {data.mttr.by_severity.length === 0 ? <Empty text="Nothing resolved yet. Fix a finding and rescan." /> : (
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={data.mttr.by_severity} layout="vertical" margin={{ top: 8, right: 24, bottom: 0, left: 8 }}>
                    <CartesianGrid stroke={GRID} horizontal={false} />
                    <XAxis type="number" tick={tick} axisLine={false} tickLine={false} unit=" h" />
                    <YAxis type="category" dataKey="severity" tick={tick} axisLine={false} tickLine={false} width={64} />
                    <Tooltip {...tooltipStyle} cursor={{ fill: "#ffffff08" }} formatter={(v: number, _n, p: any) => [`${v} h (${p.payload.count} resolved)`, "Avg"]} />
                    <Bar dataKey="avg_hours" fill={SERIES} radius={[0, 4, 4, 0]} barSize={14} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Card>

            <Card title="LLM spend per day" subtitle="USD, from token usage recorded on every provider call">
              {data.llm.daily.length === 0 ? <Empty text="No LLM calls in this window." /> :
                data.llm.total_cost_usd === 0 ? <Empty text={`$0 recorded across ${data.llm.calls} calls: self-hosted or unpriced models.`} /> : (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={data.llm.daily} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="date" tickFormatter={day} tick={tick} axisLine={{ stroke: GRID }} tickLine={false} minTickGap={24} />
                    <YAxis tick={tick} axisLine={false} tickLine={false} tickFormatter={(v) => `$${v}`} />
                    <Tooltip {...tooltipStyle} cursor={{ fill: "#ffffff08" }} labelFormatter={(d) => day(d as string)}
                      formatter={(v: number, _n, p: any) => [`$${v.toFixed(4)} · ${p.payload.calls} calls · ${p.payload.errors} errors`, "Spend"]} />
                    <Bar dataKey="cost_usd" fill={SERIES} radius={[4, 4, 0, 0]} maxBarSize={18} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </Card>

            <Card title="Provider reliability" subtitle="Outcome of every attempt, including failovers and open circuits">
              <SimpleTable head={["Provider", "OK", "Errors", "Other"]} rows={Object.entries(data.llm.by_provider).map(([p, st]) => {
                const ok = st.ok ?? 0, err = st.error ?? 0;
                return [p, ok, err, Object.entries(st).filter(([k]) => k !== "ok" && k !== "error").map(([k, v]) => `${k} ${v}`).join(", ") || "—"];
              })} empty="No LLM calls yet." />
            </Card>
          </div>

          <Card title="Repository health" subtitle="Lowest score first. Score = 100·e^(−weighted findings per KLOC ÷ 25)">
            <SimpleTable head={["Repository", "Score", "Grade", "Critical", "High", "Medium", "Low", "KLOC", "Scans", "30-day outlook"]}
              rows={data.repos.map((r) => [r.name, r.health_score.toFixed(1), r.grade, r.open_by_severity.critical ?? 0, r.open_by_severity.high ?? 0,
                r.open_by_severity.medium ?? 0, r.open_by_severity.low ?? 0, r.kloc, r.scans,
                r.forecast ? `${r.forecast.trend} → ${r.forecast.projected} pts` : "needs 3 scans"])} empty="No repositories." />
          </Card>

          <Card title="AI suggestion quality by prompt version" subtitle="Developer votes per prompt version. A falling approval rate signals prompt drift">
            <SimpleTable head={["Prompt version / source", "Findings", "Useful", "Not useful", "Approval"]}
              rows={data.prompts.map((p) => [p.prompt_version, p.issues, p.up, p.down, p.approval == null ? "—" : `${Math.round(p.approval * 100)}%`])}
              empty="No findings yet." />
          </Card>
        </>
      )}
    </main>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="bg-ink-950 px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-ink-500">{label}</div>
      <div className="mt-1 font-mono text-2xl font-medium tabular-nums text-ink-100">{value}</div>
      <div className="mt-0.5 line-clamp-2 text-xs text-ink-500" title={sub}>{sub}</div>
    </div>
  );
}

function Card({ title, subtitle, action, children }: { title: string; subtitle?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="mb-6 rounded-md border border-ink-800 bg-ink-900/30 p-4">
      <div className="mb-3 flex items-start gap-3">
        <div className="mr-auto"><h3 className="font-medium text-ink-100">{title}</h3>{subtitle && <p className="text-xs text-ink-500">{subtitle}</p>}</div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="grid h-40 place-items-center text-sm text-ink-500">{text}</div>;
}

function SimpleTable({ head, rows, empty }: { head: string[]; rows: (string | number)[][]; empty: string }) {
  if (!rows.length) return <Empty text={empty} />;
  return (
    <div className="overflow-x-auto"><table className="w-full text-sm tabular-nums">
      <thead className="text-left text-xs text-ink-500"><tr>{head.map((h, i) => <th key={h} className={`py-1 pr-3 font-normal ${i ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-ink-800">{r.map((c, j) => <td key={j} className={`py-1.5 pr-3 ${j ? "text-right" : ""}`}>{c}</td>)}</tr>)}</tbody>
    </table></div>
  );
}
