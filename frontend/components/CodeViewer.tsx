"use client";
import dynamic from "next/dynamic";
import { useEffect, useRef } from "react";
import type { FileData } from "@/lib/types";


const Editor = dynamic(async () => {
  const mod = await import("@monaco-editor/react");
  mod.loader.config({ paths: { vs: "/monaco/vs" } });
  return mod.default;
}, { ssr: false, loading: () => <div className="p-4 text-sm text-ink-500">Loading editor…</div> });

export default function CodeViewer({ file, focusLine, onPinClick }: {
  file: FileData; focusLine: number | null; onPinClick: (issueId: number) => void;
}) {
  const editorRef = useRef<any>(null);
  const monacoRef = useRef<any>(null);
  const decoRef = useRef<any>(null);


  const fileRef = useRef(file);
  const pinRef = useRef(onPinClick);
  fileRef.current = file;
  pinRef.current = onPinClick;

  const apply = () => {
    const ed = editorRef.current, monaco = monacoRef.current;
    if (!ed || !monaco) return;
    decoRef.current?.clear();
    decoRef.current = ed.createDecorationsCollection(fileRef.current.issues.map((i) => ({
      range: new monaco.Range(i.line_start, 1, i.line_end, 1),
      options: {
        isWholeLine: true, className: `hl-${i.severity}${i.finding_status === "dismissed" ? " opacity-40" : ""}`,
        glyphMarginClassName: `pin pin-${i.severity}`,
        glyphMarginHoverMessage: { value: `**[${i.severity.toUpperCase()}] ${i.title}**\n\n${i.description}${i.suggestion ? `\n\n**Suggested fix:** ${i.suggestion}` : ""}` },
        hoverMessage: { value: `**${i.title}** — ${i.description}` },
      },
    })));
  };

  useEffect(apply, [file]);
  useEffect(() => {
    if (focusLine && editorRef.current) { editorRef.current.revealLineInCenter(focusLine); editorRef.current.setPosition({ lineNumber: focusLine, column: 1 }); }
  }, [focusLine, file]);

  return (
    <Editor
      height="100%" theme="devpulse" path={file.path} language={file.language} value={file.content}
      options={{ readOnly: true, glyphMargin: true, minimap: { enabled: false }, scrollBeyondLastLine: false, stickyScroll: { enabled: false }, fontSize: 13, fontFamily: "var(--font-mono), monospace" }}
      beforeMount={(monaco) => monaco.editor.defineTheme("devpulse", { base: "vs-dark", inherit: true, rules: [], colors: {
        "editor.background": "#0b0d0c", "editorGutter.background": "#0b0d0c", "editor.lineHighlightBackground": "#141816",
        "editorLineNumber.foreground": "#45504a", "editorLineNumber.activeForeground": "#95a09a", "editorIndentGuide.background": "#1f2522" } })}
      onMount={(ed, monaco) => {
        editorRef.current = ed; monacoRef.current = monaco; apply();
        ed.onMouseDown((e: any) => {
          if (e.target.type !== monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN) return;
          const line = e.target.position?.lineNumber;
          const hit = fileRef.current.issues.find((i) => line >= i.line_start && line <= i.line_end);
          if (hit) pinRef.current(hit.id);
        });
        if (focusLine) ed.revealLineInCenter(focusLine);
      }}
    />
  );
}
