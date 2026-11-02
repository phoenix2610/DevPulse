"use client";
import { memo, useMemo, useState } from "react";
import { FixedSizeList, ListChildComponentProps } from "react-window";
import { Issue, SEV_STYLE, SEVERITIES, Severity } from "@/lib/types";
import { useSize } from "@/lib/useSize";

const ROW_H = 58;

export function SeverityBadge({ s }: { s: Severity }) {
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${SEV_STYLE[s]}`}>{s}</span>;
}


export default function IssueList({ issues, activeId, onPick, onVote, onFindingStatus }: {
  issues: Issue[]; activeId: number | null; onPick: (i: Issue) => void;
  onVote: (i: Issue, vote: 1 | -1 | null) => void; onFindingStatus: (i: Issue, status: "open" | "dismissed") => void;
}) {
  const [sev, setSev] = useState<Severity | "all">("all");
  const [hideDismissed, setHideDismissed] = useState(true);
  const [ref, size] = useSize<HTMLDivElement>();
  const shown = useMemo(() => issues.filter((i) => (sev === "all" || i.severity === sev) &&
    !(hideDismissed && i.finding_status === "dismissed")), [issues, sev, hideDismissed]);
  const counts = useMemo(() => Object.fromEntries(SEVERITIES.map((s) => [s, issues.filter((i) => i.severity === s).length])), [issues]);
  const active = issues.find((i) => i.id === activeId) ?? null;
  const data = useMemo(() => ({ shown, activeId, onPick }), [shown, activeId, onPick]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b border-ink-800 p-2">
        {(["all", ...SEVERITIES] as const).map((s) => (
          <button key={s} onClick={() => setSev(s)} aria-pressed={sev === s}
            className={`rounded px-2 py-0.5 text-xs ${sev === s ? "bg-ink-200 text-ink-950" : "bg-ink-800 text-ink-300 hover:bg-ink-700"}`}>
            {s}{s !== "all" && ` ${counts[s]}`}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1 text-xs text-ink-400">
          <input type="checkbox" checked={hideDismissed} onChange={(e) => setHideDismissed(e.target.checked)} /> hide dismissed
        </label>
      </div>
      <div ref={ref} className="min-h-0 flex-1">
        {shown.length === 0 ? <p className="p-4 text-sm text-ink-500">No issues.</p> : size.height > 0 && (
          <FixedSizeList height={size.height} width={size.width} itemCount={shown.length} itemSize={ROW_H} itemData={data}
            itemKey={(i, d) => d.shown[i].id} overscanCount={8}>
            {IssueRow}
          </FixedSizeList>
        )}
      </div>
      {active && <IssueDetail issue={active} onVote={onVote} onFindingStatus={onFindingStatus} />}
    </div>
  );
}

const IssueRow = memo(function IssueRow({ index, style, data }: ListChildComponentProps<{ shown: Issue[]; activeId: number | null; onPick: (i: Issue) => void }>) {
  const i = data.shown[index];
  return (
    <button style={style} onClick={() => data.onPick(i)}
      className={`block w-full border-b border-ink-800 px-3 py-2 text-left hover:bg-ink-900 ${data.activeId === i.id ? "bg-ink-900" : ""} ${i.finding_status === "dismissed" ? "opacity-50" : ""}`}>
      <div className="flex items-center gap-2"><SeverityBadge s={i.severity} /><span className="truncate text-sm font-medium">{i.title}</span>
        {i.source !== "static" && <span className="ml-auto shrink-0 rounded-sm border border-pulse-500/40 px-1 text-[10px] text-pulse-300">AI</span>}</div>
      <div className="mt-1 truncate font-mono text-xs text-ink-500">{i.file_path}:{i.line_start}</div>
    </button>
  );
});

function IssueDetail({ issue: i, onVote, onFindingStatus }: {
  issue: Issue; onVote: (i: Issue, v: 1 | -1 | null) => void; onFindingStatus: (i: Issue, s: "open" | "dismissed") => void;
}) {
  const vote = (v: 1 | -1) => onVote(i, i.my_vote === v ? null : v);
  return (
    <div className="max-h-[45%] space-y-2 overflow-auto border-t border-ink-700 bg-ink-900/60 p-3 text-xs text-ink-300">
      <div className="flex items-center gap-2"><SeverityBadge s={i.severity} /><span className="font-medium text-ink-100">{i.title}</span></div>
      <p>{i.description}</p>
      {i.suggestion && <div className="border-l-2 border-pulse-500 bg-ink-950/60 py-1.5 pl-2.5 pr-2"><div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-pulse-400">Suggested fix</div><p className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-ink-200">{i.suggestion}</p></div>}
      <p className="text-ink-500">{i.agent} · {i.source}{i.prompt_version && ` · ${i.prompt_version}`}{i.rule_ref && ` · ${i.rule_ref}`}</p>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button aria-pressed={i.my_vote === 1} onClick={() => vote(1)}
          className={`rounded px-2 py-0.5 ${i.my_vote === 1 ? "bg-pulse-500 text-ink-950" : "bg-ink-800 hover:bg-ink-700"}`}>Useful</button>
        <button aria-pressed={i.my_vote === -1} onClick={() => vote(-1)}
          className={`rounded px-2 py-0.5 ${i.my_vote === -1 ? "bg-red-600 text-white" : "bg-ink-800 hover:bg-ink-700"}`}>Not useful</button>
        {i.finding_id && (
          <button onClick={() => onFindingStatus(i, i.finding_status === "dismissed" ? "open" : "dismissed")}
            title="Mark as a false positive or an accepted risk; it stays dismissed in later scans"
            className="ml-auto rounded bg-ink-800 px-2 py-0.5 hover:bg-ink-700">
            {i.finding_status === "dismissed" ? "Reopen" : "Dismiss"}
          </button>
        )}
      </div>
    </div>
  );
}
