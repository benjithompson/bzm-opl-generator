// The page is one thing at a time: the form, or the manifests it produces.
//
// The preview used to sit in a sticky right-hand column, which cost the form
// half the width for the whole session. Tabs give the form all of it and the
// preview all of it, and the count on the tab is what says the bundle exists
// while it is not on screen. The cost is real and was weighed: the preview no
// longer re-renders in front of you as you type. A slide-over that keeps both
// visible was the runner-up (PROTOTYPE.md, variant F) and is the thing to
// revisit if watching it live turns out to matter.
//
// The form is hidden, never unmounted: a remount would drop scroll position and
// every open group's state on each tab switch.
import { ReactNode, useCallback, useEffect, useState } from "react";
import { GeneratedFile } from "../api";

interface WorkAreaProps {
  files: GeneratedFile[];
  activeFile: string | null;
  setActiveFile: (name: string | null) => void;
  genErr: string | null;
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
  const [tab, setTab] = useState<"configure" | "preview">("configure");
  const files = p.files;
  const idx = Math.max(0, files.findIndex((f) => f.name === p.activeFile));
  const file = files[idx];

  const go = useCallback((d: number) => {
    if (!files.length) return;
    p.setActiveFile(files[(idx + d + files.length) % files.length].name);
  }, [files, idx, p]);

  // ArrowLeft/Right cycle files, but only while the preview is the tab in view:
  // bound unconditionally they would fight every text field in the form.
  useEffect(() => {
    if (tab !== "preview") return;
    const h = (e: KeyboardEvent) => {
      if (inEditable(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [go, tab]);

  const tabCls = (on: boolean) =>
    "px-3 py-1.5 text-xs font-medium rounded-md "
    + (on ? "bg-white shadow-sm text-slate-900"
          : "text-slate-500 hover:text-slate-700");

  return (
    <>
      <div className="sticky top-0 z-20 px-6 py-2 bg-slate-50/90 backdrop-blur border-b border-slate-200">
        <div className="max-w-screen-xl mx-auto">
          <div className="inline-flex gap-1 bg-slate-200/70 rounded-lg p-1">
            <button className={tabCls(tab === "configure")}
              onClick={() => setTab("configure")}>
              Configure
            </button>
            <button className={tabCls(tab === "preview")}
              onClick={() => setTab("preview")}>
              Preview {files.length > 0 && `(${files.length})`}
            </button>
          </div>
        </div>
      </div>

      <div className={tab === "configure" ? "" : "hidden"}>{p.children}</div>

      <div className={tab === "preview"
        ? "max-w-screen-xl mx-auto px-6 py-6 h-[calc(100vh-7rem)]" : "hidden"}>
        <div className="bg-slate-900 rounded-xl shadow-lg overflow-hidden h-full flex flex-col">
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
            <button className={barBtn} onClick={() => setTab("configure")}>
              ← back to form
            </button>
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
      </div>
    </>
  );
}
