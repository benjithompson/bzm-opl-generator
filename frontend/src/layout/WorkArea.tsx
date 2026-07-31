// The manifests, in a drawer that slides in from the right.
//
// This is the third answer to the same question. The first was a sticky
// right-hand column, which cost the form half its width for the whole session.
// The second was a "Configure | Preview" pair of tabs, which gave each of them
// the full page but made the page one thing at a time. A drawer keeps what both
// were for: the form has everything while the drawer is shut, and the drawer
// covers what it needs when it is open -- over the top, rather than by
// squeezing what is underneath.
//
// The form is hidden behind it, never unmounted: a remount would drop scroll
// position and every open group's state each time the preview was opened.
import { ReactNode, useCallback, useEffect } from "react";
import { GeneratedFile } from "../api";

interface WorkAreaProps {
  files: GeneratedFile[];
  activeFile: string | null;
  setActiveFile: (name: string | null) => void;
  genErr: string | null;
  open: boolean;
  setOpen: (v: boolean) => void;
  /** The steps. */
  children: ReactNode;
}

const barBtn =
  "text-slate-300 hover:text-white border border-slate-600 rounded px-2 py-0.5 "
  + "text-xs disabled:opacity-40";

const inEditable = (t: EventTarget | null) => {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable;
};

export function WorkArea(p: WorkAreaProps) {
  const files = p.files;
  const idx = Math.max(0, files.findIndex((f) => f.name === p.activeFile));
  const file = files[idx];

  const go = useCallback((d: number) => {
    if (!files.length) return;
    p.setActiveFile(files[(idx + d + files.length) % files.length].name);
  }, [files, idx, p]);

  // ArrowLeft/Right cycle files, but only while the drawer is open: bound
  // unconditionally they would fight every text field in the form. Escape
  // closes it, which is what a slide-over is expected to do.
  useEffect(() => {
    if (!p.open) return;
    const h = (e: KeyboardEvent) => {
      if (inEditable(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
      else if (e.key === "Escape") p.setOpen(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [go, p]);

  return (
    <>
      {p.children}

      {/* The handle, on the edge the drawer comes from. Always there, and the
          count on it is what says a bundle exists while it is out of sight --
          the job the "Preview (8)" tab used to do. It hides while the drawer is
          open, because the drawer covers the edge it sits on. */}
      <button
        onClick={() => p.setOpen(true)}
        aria-expanded={p.open}
        aria-label="Open the manifest preview"
        className={"fixed right-0 top-1/2 -translate-y-1/2 z-30 flex items-center gap-1 "
          + "bg-slate-900 text-white rounded-l-lg pl-1.5 pr-1 py-3 shadow-lg "
          + "hover:bg-slate-800 transition-opacity "
          + (p.open ? "opacity-0 pointer-events-none" : "opacity-100")}>
        <svg viewBox="0 0 20 20" className="w-4 h-4" fill="none" stroke="currentColor"
          strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 5l-5 5 5 5" />
        </svg>
        <span className="text-[11px] font-medium [writing-mode:vertical-rl] py-1">
          Preview{files.length > 0 ? ` (${files.length})` : ""}
        </span>
      </button>

      {/* Click-away, and it dims the form so the drawer reads as being over it
          rather than beside it. */}
      <div onClick={() => p.setOpen(false)}
        className={"fixed inset-0 z-30 bg-slate-900/20 transition-opacity duration-200 "
          + (p.open ? "opacity-100" : "opacity-0 pointer-events-none")} />

      <aside aria-hidden={!p.open}
        className={"fixed right-0 top-0 bottom-0 z-40 w-full max-w-3xl p-3 "
          + "transition-transform duration-200 ease-out "
          + (p.open ? "translate-x-0" : "translate-x-full")}>
        <div className="bg-slate-900 rounded-xl shadow-2xl overflow-hidden h-full flex flex-col">
          <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-800">
            <select
              className="grow min-w-0 max-w-md bg-slate-800 text-slate-100 text-xs font-mono rounded px-2 py-1 border border-slate-700"
              value={file?.name ?? ""}
              onChange={(e) => p.setActiveFile(e.target.value)}>
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
            {/* Closes towards the edge it came from. */}
            <button className={barBtn} onClick={() => p.setOpen(false)}
              title="close (Esc)" aria-label="Close the preview">→</button>
          </div>
          {p.genErr && <p className="text-red-400 text-xs px-4 py-2">{p.genErr}</p>}
          {file ? (
            <pre className="flex-1 min-h-0 text-[11.5px] leading-relaxed text-slate-100 p-4 pt-3 overflow-auto font-mono">
              {file.content}
            </pre>
          ) : (
            !p.genErr && (
              <p className="text-slate-500 text-sm p-4">
                pick a location &amp; agent to preview manifests
              </p>
            )
          )}
        </div>
      </aside>
    </>
  );
}
