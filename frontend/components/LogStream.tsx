"use client";
import { useEffect, useRef } from "react";
import type { LogEvent } from "@/lib/types";

const COLORS: Record<string, string> = { error: "text-red-400", warn: "text-yellow-400", info: "text-ink-300" };

export default function LogStream({ logs }: { logs: LogEvent[] }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [logs.length]);
  return (
    <div className="h-full overflow-auto bg-black/40 p-3 font-mono text-xs">
      {logs.map((l) => (
        <div key={l.seq} className={COLORS[l.level] ?? COLORS.info}>
          <span className="text-ink-600">{new Date(l.ts).toLocaleTimeString()}</span>{" "}
          <span className="text-ink-400">[{l.agent}]</span> {l.message}
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}
