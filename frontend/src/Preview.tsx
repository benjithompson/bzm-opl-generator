// Manifest preview pane ("command-bar" layout): file select + prev/next +
// n-of-m counter in one compact header, content filling the page height.
// ArrowLeft/ArrowRight cycle files (unless typing in a field); the ⛶ button
// expands to a fullscreen overlay (Esc or backdrop click to exit).
import { useCallback, useEffect, useState } from "react";
import { GeneratedFile } from "./api";

interface PreviewProps {
  files: GeneratedFile[];
  activeFile: string | null;
  setActiveFile: (name: string | null) => void;
  genErr: string | null;
}

const inEditable = (t: EventTarget | null) => {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable;
};

const barBtn =
  "text-slate-300 hover:text-white border border-slate-600 rounded px-2 py-0.5 text-xs disabled:opacity-40";

export function Preview({ files, activeFile, setActiveFile, genErr }: PreviewProps) {
  const [expanded, setExpanded] = useState(false);
  const idx = Math.max(0, files.findIndex((f) => f.name === activeFile));
  const file = files[idx];

  const go = useCallback((d: number) => {
    if (files.length === 0) return;
    setActiveFile(files[(idx + d + files.length) % files.length].name);
  }, [files, idx, setActiveFile]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setExpanded(false); return; }
      if (inEditable(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [go]);

  const pane = (
    <div className="bg-slate-900 rounded-xl shadow-lg overflow-hidden h-full flex flex-col">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-800">
        <select
          className="grow min-w-0 bg-slate-800 text-slate-100 text-xs font-mono rounded px-2 py-1 border border-slate-700"
          value={file?.name ?? ""}
          onChange={(e) => setActiveFile(e.target.value)}>
          {files.map((f) => (
            <option key={f.name} value={f.name}>{f.name}</option>
          ))}
        </select>
        <button className={barBtn} onClick={() => go(-1)} disabled={files.length < 2}
          title="previous file (←)">←</button>
        <span className="text-[10px] text-slate-500 whitespace-nowrap">
          {files.length === 0 ? "0 of 0" : `${idx + 1} of ${files.length}`}
        </span>
        <button className={barBtn} onClick={() => go(1)} disabled={files.length < 2}
          title="next file (→)">→</button>
        <button className={barBtn} disabled={!file}
          onClick={() => file && navigator.clipboard.writeText(file.content)}>
          copy
        </button>
        <button className={barBtn} onClick={() => setExpanded(!expanded)}
          title={expanded ? "exit fullscreen (Esc)" : "fullscreen"}>
          {expanded ? "✕" : "⛶"}
        </button>
      </div>
      {genErr && <p className="text-red-400 text-xs px-4 py-2">{genErr}</p>}
      {file ? (
        <pre className="flex-1 min-h-0 text-[11.5px] leading-relaxed text-slate-100 p-4 pt-3 overflow-auto font-mono">
          {file.content}
        </pre>
      ) : (
        !genErr && (
          <p className="text-slate-500 text-sm p-4">
            pick a location &amp; agent to preview manifests
          </p>
        )
      )}
    </div>
  );

  if (expanded) {
    return (
      <div className="fixed inset-0 z-40 bg-black/60 p-3 md:p-6"
        onClick={(e) => { if (e.target === e.currentTarget) setExpanded(false); }}>
        <div className="h-full max-w-screen-xl mx-auto">{pane}</div>
      </div>
    );
  }
  return (
    <div className="lg:sticky lg:top-16 self-start h-[calc(100vh-5.5rem)] min-h-[400px]">
      {pane}
    </div>
  );
}
