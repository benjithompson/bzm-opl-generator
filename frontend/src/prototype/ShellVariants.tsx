// THROWAWAY -- four places to put the manifest preview, so the configure step
// can have the full width instead of half of it.
//
// The pane itself is the same in all four, chooser header included (select,
// prev/next, n-of-m, copy, fullscreen): the question is only where it lives and
// what opens it.
//
//   D  bottom drawer   -- a bar pinned to the viewport, opens upward
//   E  inline panel    -- last block on the page, opens in flow
//   F  right slide-over-- floating button, panel over the right edge
//   G  top tabs        -- Configure / Preview swap the whole work area
//
// Read-only: nothing here writes an option. `files` and the active-file state
// are the real ones, handed down from App.

import { ReactNode, useCallback, useEffect, useState } from "react";
import { GeneratedFile } from "../api";

export type ShellKey = "D" | "E" | "F" | "G";

export interface ShellProps {
  files: GeneratedFile[];
  activeFile: string | null;
  setActiveFile: (name: string | null) => void;
  genErr: string | null;
  /** The configure column -- steps 1-3, full width in every shell here. */
  children: ReactNode;
}

const barBtn =
  "text-slate-300 hover:text-white border border-slate-600 rounded px-2 py-0.5 text-xs disabled:opacity-40";

/** The pane, header and all. `onClose` is what the shell wants the ✕ to do --
 *  the shells disagree about whether closing means collapse, dismiss or go back
 *  to the form, so the pane does not decide. */
function Pane(p: ShellProps & { onClose?: () => void; closeLabel?: string }) {
  const files = p.files;
  const idx = Math.max(0, files.findIndex((f) => f.name === p.activeFile));
  const file = files[idx];
  const go = useCallback((d: number) => {
    if (!files.length) return;
    p.setActiveFile(files[(idx + d + files.length) % files.length].name);
  }, [files, idx, p]);
  return (
    <div className="bg-slate-900 rounded-xl shadow-lg overflow-hidden h-full flex flex-col">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-800">
        <select
          className="grow min-w-0 max-w-md bg-slate-800 text-slate-100 text-xs font-mono rounded px-2 py-1 border border-slate-700"
          value={file?.name ?? ""}
          onChange={(e) => p.setActiveFile(e.target.value)}>
          {files.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
        </select>
        <button className={barBtn} onClick={() => go(-1)} disabled={files.length < 2}>←</button>
        <span className="text-[10px] text-slate-500 whitespace-nowrap">
          {files.length === 0 ? "0 of 0" : `${idx + 1} of ${files.length}`}
        </span>
        <button className={barBtn} onClick={() => go(1)} disabled={files.length < 2}>→</button>
        <button className={barBtn} disabled={!file}
          onClick={() => file && navigator.clipboard.writeText(file.content)}>copy</button>
        {p.onClose && (
          <button className={barBtn} onClick={p.onClose}>{p.closeLabel ?? "✕"}</button>
        )}
      </div>
      {p.genErr && <p className="text-red-400 text-xs px-4 py-2">{p.genErr}</p>}
      {file ? (
        <pre className="flex-1 min-h-0 text-[11.5px] leading-relaxed text-slate-100 p-4 pt-3 overflow-auto font-mono">
          {file.content}
        </pre>
      ) : !p.genErr && (
        <p className="text-slate-500 text-sm p-4">
          pick a location &amp; agent to preview manifests
        </p>
      )}
    </div>
  );
}

/** Files in the bundle, for whatever affordance opens the pane. The count is
 *  the point: closed, it is the only thing saying the bundle exists. */
const label = (files: GeneratedFile[]) =>
  files.length ? `Preview manifests · ${files.length} files` : "Preview manifests";

// == D -- bottom drawer =======================================================
// Pinned to the viewport, so it is reachable from anywhere on the page without
// scrolling to it, and it never takes width. Two heights only: closed to its
// own bar, and open to two thirds of the screen.
function ShellD(p: ShellProps) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  return (
    <>
      <div className={open ? "pb-[68vh]" : "pb-16"}>{p.children}</div>
      <div className={"fixed inset-x-0 bottom-0 z-30 " + (open ? "h-[66vh]" : "")}>
        {open ? (
          <div className="h-full px-3 pb-3">
            <Pane {...p} onClose={() => setOpen(false)} closeLabel="▼" />
          </div>
        ) : (
          <button onClick={() => setOpen(true)}
            className="w-full bg-slate-900 text-slate-200 text-xs px-4 py-2.5 flex items-center gap-3 hover:bg-slate-800 border-t border-slate-700">
            <span className="font-medium">{label(p.files)}</span>
            <span className="font-mono text-slate-400 truncate">
              {p.activeFile ?? ""}
            </span>
            <span className="grow" />
            <span className="text-slate-400">▲</span>
          </button>
        )}
      </div>
    </>
  );
}

// == E -- inline panel ========================================================
// In the flow of the page, after the download step: the preview is the last
// thing you look at, so it is the last thing on the page, and nothing floats.
function ShellE(p: ShellProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {p.children}
      {/* Same container as the work area it follows -- it is one more block on
          the page, not a layer over it. */}
      <div className="max-w-screen-xl mx-auto px-6 pb-8 -mt-2">
        {open ? (
          <div className="h-[75vh]">
            <Pane {...p} onClose={() => setOpen(false)} closeLabel="collapse" />
          </div>
        ) : (
          <button onClick={() => setOpen(true)}
            className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-left hover:bg-slate-50 flex items-center gap-3">
            <span className="text-sm font-medium text-slate-700">{label(p.files)}</span>
            <span className="text-[11px] text-slate-400 font-mono truncate">
              {p.files.slice(0, 3).map((f) => f.name).join("  ")}
              {p.files.length > 3 ? "  …" : ""}
            </span>
            <span className="grow" />
            <span className="text-slate-400 text-xs">open ▾</span>
          </button>
        )}
      </div>
    </>
  );
}

// == F -- right slide-over ====================================================
// Over the page rather than beside it: the form keeps its full width, and the
// preview appears where it used to live, so the muscle memory survives. Closest
// to today's page of the four, and the only one that can be open while the form
// is being edited without either of them shrinking.
function ShellF(p: ShellProps) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  return (
    <>
      {p.children}
      {!open && (
        <button onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-30 rounded-full bg-slate-900 text-white text-xs font-medium px-4 py-2.5 shadow-lg hover:bg-slate-800">
          {label(p.files)} ▸
        </button>
      )}
      {/* Below the app header, not over it: the panel is a layer on the work
          area, and covering the product's own header makes it read as a
          different page. */}
      {open && (
        <div className="fixed top-16 bottom-3 right-3 z-30 w-[44rem] max-w-[92vw]">
          <Pane {...p} onClose={() => setOpen(false)} closeLabel="✕" />
        </div>
      )}
    </>
  );
}

// == G -- top tabs ============================================================
// The work area is one thing at a time. Nothing floats and nothing overlaps;
// the cost is that the preview cannot be watched while a field is typed, which
// is the whole reason it is beside the form today.
function ShellG(p: ShellProps) {
  const [tab, setTab] = useState<"cfg" | "preview">("cfg");
  const tabCls = (on: boolean) =>
    "px-3 py-1.5 text-xs font-medium rounded-md " +
    (on ? "bg-white shadow-sm text-slate-900" : "text-slate-500 hover:text-slate-700");
  return (
    <>
      <div className="sticky top-0 z-20 px-6 py-2 bg-slate-50/90 backdrop-blur border-b border-slate-200">
        <div className="max-w-screen-xl mx-auto">
        <div className="inline-flex gap-1 bg-slate-200/70 rounded-lg p-1">
          <button className={tabCls(tab === "cfg")} onClick={() => setTab("cfg")}>
            Configure
          </button>
          <button className={tabCls(tab === "preview")} onClick={() => setTab("preview")}>
            Preview {p.files.length > 0 && `(${p.files.length})`}
          </button>
        </div>
        </div>
      </div>
      {/* Kept mounted, not unmounted: a remount would drop the scroll position
          and, in the form's case, every open group's scroll into view. */}
      <div className={tab === "cfg" ? "" : "hidden"}>{p.children}</div>
      <div className={tab === "preview"
        ? "max-w-screen-xl mx-auto px-6 py-6 h-[calc(100vh-7rem)]" : "hidden"}>
        <Pane {...p} onClose={() => setTab("cfg")} closeLabel="← back to form" />
      </div>
    </>
  );
}

/** `variant` null is the shipped page: the work area passes straight through
 *  and the preview stays where App put it. Wrapping unconditionally is what
 *  keeps the whole prototype to three lines of App. */
export function PreviewShell(p: ShellProps & { variant: ShellKey | null }) {
  if (!p.variant) return <>{p.children}</>;
  const S = { D: ShellD, E: ShellE, F: ShellF, G: ShellG }[p.variant];
  return <S {...p} />;
}
