// The manifests, in a drawer on the right that behaves like the one on the
// left: it pushes the page over rather than covering it, and it stays open
// until you close it.
//
// This is the fourth shape for the same thing, and the first three each failed
// the same way. A sticky right-hand column cost the form half its width for the
// whole session. "Configure | Preview" tabs gave each of them the page, and made
// it one thing at a time. A modal slide-over kept both alive but dimmed the form
// and closed on any click outside, so reading a manifest while editing the field
// it came from meant re-opening it every time.
//
// In the flex row, closed is a rail rather than nothing, so the way back in is
// always in the same place -- and the file count on it is what says a bundle
// exists while it is out of sight.
import { useCallback, useEffect, useRef } from "react";
import { GeneratedFile } from "../api";

const barBtn =
  "text-slate-300 hover:text-white border border-slate-600 rounded px-2 py-0.5 "
  + "text-xs disabled:opacity-40";

export function PreviewDrawer(props: {
  files: GeneratedFile[];
  activeFile: string | null;
  setActiveFile: (name: string | null) => void;
  genErr: string | null;
  open: boolean;
  setOpen: (v: boolean) => void;
}) {
  const { files, open } = props;
  const idx = Math.max(0, files.findIndex((f) => f.name === props.activeFile));
  const file = files[idx];
  const panel = useRef<HTMLDivElement>(null);

  const go = useCallback((d: number) => {
    if (!files.length) return;
    props.setActiveFile(files[(idx + d + files.length) % files.length].name);
  }, [files, idx, props]);

  // Arrow keys cycle files only while the focus is inside this panel. Bound to
  // the window while it is merely *open*, they would take the arrow keys off
  // the form beside it -- which is the cost of a drawer that no longer covers
  // what it sits next to.
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (!panel.current?.contains(e.target as Node)) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [go, open]);

  return (
    <div ref={panel}
      className={"shrink-0 border-l border-slate-200 bg-slate-50 flex flex-col "
        + "transition-[width] duration-200 ease-out overflow-hidden "
        + (open ? "w-[52rem] max-w-[60vw]" : "w-10")}>
      {open ? (
        <>
          <div className="flex items-center h-12 px-3 gap-2 border-b border-slate-200 shrink-0">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 grow">
              Preview{files.length > 0 ? ` · ${files.length} files` : ""}
            </span>
            <button
              onClick={() => props.setOpen(false)}
              aria-expanded
              aria-label="Collapse the preview"
              title="Collapse"
              className="rounded-md border border-slate-300 text-slate-500 hover:text-slate-900
                         hover:bg-slate-100 flex items-center justify-center w-8 h-8">
              {/* Points at the edge it collapses towards, like the left one. */}
              <svg viewBox="0 0 20 20" className="w-4 h-4" fill="none"
                stroke="currentColor" strokeWidth={1.75}
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 5l5 5-5 5" />
              </svg>
            </button>
          </div>
          <div className="p-2 flex-1 min-h-0">
            <div className="bg-slate-900 rounded-lg h-full flex flex-col overflow-hidden">
              <div className="flex items-center gap-2 px-2 py-2 border-b border-slate-800">
                <select
                  className="grow min-w-0 bg-slate-800 text-slate-100 text-xs font-mono rounded px-2 py-1 border border-slate-700"
                  value={file?.name ?? ""}
                  onChange={(e) => props.setActiveFile(e.target.value)}>
                  {files.map((f) => (
                    <option key={f.name} value={f.name}>{f.name}</option>
                  ))}
                </select>
                <button className={barBtn} onClick={() => go(-1)}
                  disabled={files.length < 2} title="previous file (←)">←</button>
                <span className="text-[10px] text-slate-500 whitespace-nowrap">
                  {files.length === 0 ? "0 of 0" : `${idx + 1} of ${files.length}`}
                </span>
                <button className={barBtn} onClick={() => go(1)}
                  disabled={files.length < 2} title="next file (→)">→</button>
                <button className={barBtn} disabled={!file}
                  onClick={() => file && navigator.clipboard.writeText(file.content)}>
                  copy
                </button>
              </div>
              {props.genErr && (
                <p className="text-red-400 text-xs px-3 py-2">{props.genErr}</p>
              )}
              {file ? (
                <pre className="flex-1 min-h-0 text-[11.5px] leading-relaxed text-slate-100 p-3 overflow-auto font-mono">
                  {file.content}
                </pre>
              ) : (
                !props.genErr && (
                  <p className="text-slate-500 text-sm p-3">
                    pick a location &amp; agent to preview manifests
                  </p>
                )
              )}
            </div>
          </div>
        </>
      ) : (
        // The rail. Vertical, so the label fits the width the closed drawer has,
        // and the whole strip is the control rather than a button floating on it.
        <button
          onClick={() => props.setOpen(true)}
          aria-expanded={false}
          aria-label="Open the manifest preview"
          title="Preview"
          className="h-full w-full flex flex-col items-center gap-2 pt-3
                     text-slate-500 hover:text-slate-900 hover:bg-slate-100">
          <svg viewBox="0 0 20 20" className="w-4 h-4 shrink-0" fill="none"
            stroke="currentColor" strokeWidth={1.75}
            strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 5l-5 5 5 5" />
          </svg>
          <span className="text-[11px] font-medium [writing-mode:vertical-rl]">
            Preview{files.length > 0 ? ` (${files.length})` : ""}
          </span>
        </button>
      )}
    </div>
  );
}
