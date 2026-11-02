"use client";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { FixedSizeList, ListChildComponentProps } from "react-window";
import { api } from "@/lib/api";
import type { TreeEntry } from "@/lib/types";
import { useSize } from "@/lib/useSize";

const ROW_H = 26;
type Loaded = TreeEntry[] | "loading" | { error: string };
type Row = { kind: "entry"; entry: TreeEntry; depth: number } | { kind: "status"; text: string; depth: number };


export default function FileTree({ jobId, selected, onSelect, refreshKey }: {
  jobId: string; selected: string | null; onSelect: (p: string) => void; refreshKey: unknown;
}) {
  const [nodes, setNodes] = useState<Map<string, Loaded>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [ref, size] = useSize<HTMLDivElement>();

  const load = useCallback((path: string) => {
    setNodes((m) => new Map(m).set(path, "loading"));
    api<TreeEntry[]>(`/jobs/${jobId}/tree?path=${encodeURIComponent(path)}`)
      .then((e) => setNodes((m) => new Map(m).set(path, e)))
      .catch((e) => setNodes((m) => new Map(m).set(path, { error: e.message })));
  }, [jobId]);

  useEffect(() => { setNodes(new Map()); setExpanded(new Set()); load(""); }, [load, refreshKey]);

  const toggle = useCallback((e: TreeEntry) => {
    if (e.type === "file") return onSelect(e.path);
    const opening = !expanded.has(e.path);
    if (opening && !nodes.has(e.path)) load(e.path);
    setExpanded((s) => { const next = new Set(s); if (opening) next.add(e.path); else next.delete(e.path); return next; });
  }, [expanded, nodes, load, onSelect]);

  const rows = useMemo(() => {
    const out: Row[] = [];
    const walk = (path: string, depth: number) => {
      const n = nodes.get(path);
      if (n === undefined || n === "loading") return void out.push({ kind: "status", text: "Loading…", depth });
      if ("error" in n) return void out.push({ kind: "status", depth,
        text: depth === 0 && !/expired/i.test(n.error) ? "Files appear once cloning finishes…" : n.error });
      if (depth === 0 && n.length === 0) return void out.push({ kind: "status", text: "Files appear once cloning finishes…", depth });
      for (const entry of n) {
        out.push({ kind: "entry", entry, depth });
        if (entry.type === "dir" && expanded.has(entry.path)) walk(entry.path, depth + 1);
      }
    };
    walk("", 0);
    return out;
  }, [nodes, expanded]);

  const data = useMemo(() => ({ rows, selected, expanded, toggle }), [rows, selected, expanded, toggle]);
  return (
    <div ref={ref} className="h-full" role="tree" aria-label="Repository files">
      {size.height > 0 && (
        <FixedSizeList height={size.height} width={size.width} itemCount={rows.length} itemSize={ROW_H}
          itemData={data} itemKey={(i, d) => { const r = d.rows[i]; return r.kind === "entry" ? r.entry.path : `s:${i}`; }} overscanCount={12}>
          {TreeRow}
        </FixedSizeList>
      )}
    </div>
  );
}

const TreeRow = memo(function TreeRow({ index, style, data }: ListChildComponentProps<{
  rows: Row[]; selected: string | null; expanded: Set<string>; toggle: (e: TreeEntry) => void;
}>) {
  const row = data.rows[index];
  const pad = 8 + row.depth * 14;
  if (row.kind === "status") return <div style={{ ...style, paddingLeft: pad + 18 }} title={row.text} className="truncate pr-2 text-xs leading-[26px] text-ink-500">{row.text}</div>;
  const e = row.entry;
  const open = data.expanded.has(e.path);
  return (
    <button style={{ ...style, paddingLeft: pad }} onClick={() => data.toggle(e)} role="treeitem" aria-expanded={e.type === "dir" ? open : undefined}
      aria-selected={data.selected === e.path} title={e.path}
      className={`flex items-center gap-1.5 pr-2 text-left text-sm hover:bg-ink-800 ${data.selected === e.path ? "bg-ink-800 text-white" : ""}`}>
      <span className="w-3 shrink-0 text-ink-500">{e.type === "dir" ? (open ? "▾" : "▸") : ""}</span>
      <span className="truncate">{e.name}</span>
      {e.issue_count > 0 && <span className="ml-auto shrink-0 rounded-sm bg-red-500/20 px-1.5 text-xs text-red-300">{e.issue_count}</span>}
    </button>
  );
});
