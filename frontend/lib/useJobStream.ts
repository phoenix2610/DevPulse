"use client";
import { useEffect, useRef, useState } from "react";
import { getToken, wsUrl } from "./api";
import type { JobStatus, LogEvent } from "./types";


export function useJobStream(jobId: string, onDone: (status: JobStatus) => void) {
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [progress, setProgress] = useState(0);
  const [connected, setConnected] = useState(false);
  const doneRef = useRef(false);
  const cb = useRef(onDone);
  cb.current = onDone;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let attempt = 0;
    let closed = false;
    const seen = new Set<number>();

    const connect = () => {
      ws = new WebSocket(wsUrl(`/ws/jobs/${jobId}`));

      ws.onopen = () => { ws?.send(JSON.stringify({ token: getToken() ?? "" })); setConnected(true); attempt = 0; };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (ev.type === "done") { doneRef.current = true; cb.current(ev.status); return; }
        if (ev.type !== "log" || seen.has(ev.seq)) return;
        seen.add(ev.seq);
        setLogs((l) => [...l, ev]);
        if (typeof ev.progress === "number") setProgress((p) => Math.max(p, ev.progress));
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed && !doneRef.current) timer = setTimeout(connect, Math.min(1000 * 2 ** attempt++, 10000));
      };
    };
    connect();
    return () => { closed = true; clearTimeout(timer); ws?.close(); };
  }, [jobId]);

  return { logs, progress, connected };
}
