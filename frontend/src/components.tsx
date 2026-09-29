import { ReactNode, useEffect, useMemo, useRef, useState } from "react";

export function Section(props: {
  n: number; title: string; hint?: string; done?: boolean; children: ReactNode;
}) {
  return (
    <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
      <div className="flex items-center gap-3 mb-3">
        <span className={`w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold text-white ${props.done ? "bg-emerald-500" : "bg-bzm"}`}>
          {props.done ? "✓" : props.n}
        </span>
        <div>
          <h2 className="font-semibold text-slate-900 leading-tight">{props.title}</h2>
          {props.hint && <p className="text-xs text-slate-500">{props.hint}</p>}
        </div>
      </div>
      {props.children}
    </section>
  );
}

/** The red asterisk on a required field's label, with a word for screen
 *  readers. It says the field must be filled, not whether it is. */
function RequiredMark() {
  return (
    <>
      <span aria-hidden="true" className="text-red-600">*</span>
      <span className="sr-only">(required)</span>
    </>
  );
}

export function Field(props: {
  label: string; hint?: string; children: ReactNode;
  /** Marks the label with the asterisk. */
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">
        {props.label}{props.required && <RequiredMark />}
      </span>
      {props.children}
      {props.hint && <span className="text-2xs text-slate-400">{props.hint}</span>}
    </label>
  );
}

export const inputCls =
  "mt-0.5 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm " +
  "focus:outline-none focus:ring-2 focus:ring-bzm/40 focus:border-bzm bg-white";

export function TextInput(props: {
  value: string; onChange: (v: string) => void; placeholder?: string;
  mono?: boolean;
  /** Shown but not editable, for a field describing a state the page is in. */
  disabled?: boolean;
}) {
  return (
    <input
      className={inputCls + (props.mono ? " font-mono text-xs" : "")
        + (props.disabled ? " bg-slate-50 text-slate-500" : "")}
      value={props.value}
      placeholder={props.placeholder}
      disabled={props.disabled}
      onChange={(e) => props.onChange(e.target.value)}
    />
  );
}

/** A credential field: masked, with a deliberate Show. Masking guards against
 *  screen shares, not readers of the cluster. `type=password`, so password
 *  managers and screen readers understand it. */
export function SecretInput(props: {
  value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  const [shown, setShown] = useState(false);
  return (
    <div className="flex gap-1.5 items-start">
      <input
        className={inputCls + " font-mono text-xs"}
        type={shown ? "text" : "password"}
        autoComplete="off" spellCheck={false}
        value={props.value}
        placeholder={props.placeholder}
        onChange={(e) => props.onChange(e.target.value)}
      />
      <button type="button" aria-pressed={shown} onClick={() => setShown(!shown)}
        className={"mt-0.5 shrink-0 rounded-md border border-slate-300 px-2 py-1.5 "
          + "text-xs font-medium text-slate-600 hover:bg-slate-50"}>
        {shown ? "Hide" : "Show"}
      </button>
    </div>
  );
}

export function Check(props: {
  label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string;
  /** Shown but not changeable: a box describing a state rather than offering one. */
  disabled?: boolean;
}) {
  return (
    <label className={"flex items-start gap-2 text-sm select-none "
      + (props.disabled ? "opacity-50" : "cursor-pointer")}>
      <input
        type="checkbox"
        className="mt-0.5 accent-bzm"
        checked={props.checked}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.checked)}
      />
      <span>
        {props.label}
        {props.hint && <span className="block text-2xs text-slate-400">{props.hint}</span>}
      </span>
    </label>
  );
}

/** A whole-number field. A blank string means "not given". */
export function NumberInput(props: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  min?: number;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <input type="number" min={props.min ?? 1}
      className={inputCls + (props.className ? " " + props.className : "")}
      placeholder={props.placeholder} value={props.value}
      disabled={props.disabled}
      onChange={(e) => props.onChange(e.target.value)} />
  );
}

/** The white card these pages sit in. */
export const cardCls =
  "bg-white border border-slate-200 rounded-lg p-4 space-y-3";

/** One number out of a plan: the figure, what it counts, what it costs. `big`
 *  for the sizing card, compact inside a location. */
export function Figure(props: {
  n: number | string; unit: string; sub: string; big?: boolean;
}) {
  return (
    <div className={"border border-slate-200 rounded-md "
      + (props.big ? "p-3" : "px-2.5 py-2")}>
      <div className={"font-bold text-slate-900 leading-none "
        + (props.big ? "text-2xl" : "text-lg")}>{props.n}</div>
      <div className={"font-medium text-slate-600 "
        + (props.big ? "text-xs mt-1" : "text-2xs mt-0.5")}>{props.unit}</div>
      <div className={"text-slate-400 "
        + (props.big ? "text-2xs mt-0.5" : "text-3xs")}>{props.sub}</div>
    </div>
  );
}

/** What a plan assumes and what it warns about, the warnings in plan.py's own
 *  words. `compact` inside a location. */
export function PlanCaveats(props: {
  warnings: string[];
  compact?: boolean;
  /** Every model the plan was asked for; each assumed figure gets its own note
   *  in its own unit. An unmeasured model has none: the server's warning covers it. */
  sizings: { per_pod: number | null; per_pod_unit: string;
             per_pod_source: string }[];
}) {
  const small = props.compact;
  const assumed = props.sizings
    .filter((s) => s.per_pod_source === "assumed")
    .map((s) => ({ figure: s.per_pod ?? 0, unit: s.per_pod_unit }));
  return (
    <>
      {assumed.map((a) => (
        <div key={a.unit}
          className={small ? "" : "border border-amber-300 bg-amber-50 rounded-lg p-3"}>
          <p className={small ? "text-2xs text-amber-700" : "text-xs text-amber-900"}>
            <b>{a.figure.toLocaleString()} {a.unit} is
            assumed</b>, not measured — it is what a pod of this size is
            rated for. How much one pod really carries depends
            on what your test does, and every number above is
            that figure multiplied out. Run the real thing against one pod,
            find where it saturates, and put that number in the field above.
          </p>
        </div>
      ))}
      {props.warnings.map((w) => (
        <div key={w}
          className={small ? "" : "border border-slate-200 bg-slate-50 rounded-lg p-3"}>
          <p className={small ? "text-2xs text-slate-500" : "text-xs text-slate-600"}>
            {w}
          </p>
        </div>
      ))}
    </>
  );
}

/** Indeterminate progress, in `currentColor` so it suits either button kind. */
export function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg className={"animate-spin h-3.5 w-3.5 shrink-0 " + className}
      viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10"
        stroke="currentColor" strokeWidth="4" />
      <path className="opacity-90" fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  );
}

export function Button(props: {
  onClick: () => void; children: ReactNode; kind?: "primary" | "ghost";
  disabled?: boolean;
  /** Fill the given width and centre the label, so a changing label does not
   *  shift its neighbours. */
  block?: boolean;
  /** In flight: shows a spinner and blocks a second click. Separate from
   *  `disabled`, which means not allowed. */
  busy?: boolean;
}) {
  const base = "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm "
    + "font-medium transition-colors disabled:opacity-40"
    + (props.block ? " w-full justify-center" : "");
  const kinds = {
    primary: "bg-bzm text-white hover:bg-bzm-dark",
    ghost: "border border-slate-300 text-slate-600 hover:bg-slate-50",
  };
  return (
    <button type="button" className={`${base} ${kinds[props.kind ?? "primary"]}`}
      onClick={props.onClick} disabled={props.disabled || props.busy}
      aria-busy={props.busy || undefined}>
      {props.busy && <Spinner />}
      {props.children}
    </button>
  );
}

/** `label` names the switch for screen readers and tests; a GroupRow's row
 *  already labels its own. */
export function Switch({ on, onChange, label }: {
  on: boolean; onChange: (v: boolean) => void; label?: string;
}) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label}
      onClick={() => onChange(!on)}
      className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${on ? "bg-bzm" : "bg-slate-300"}`}>
      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

/** One panel of a step: a bordered card with a header, optionally collapsible
 *  (the whole header bar is then the control, with a chevron). */
export function SubSection(props: {
  title: string; hint?: string; done?: boolean; children: ReactNode;
  /** Collapsible when both are given, controlled by the caller, which knows
   *  what should be open. Given neither, always open. */
  open?: boolean;
  onToggle?: () => void;
  /** A word or two on the header, visible while collapsed. */
  summary?: string;
  /** A control on the header row, beside the toggle rather than inside it (a
   *  button inside a button would swallow its clicks). */
  action?: ReactNode;
}) {
  const collapsible = props.open !== undefined && !!props.onToggle;
  const open = !collapsible || !!props.open;
  const heading = (
    <>
      {collapsible && <Chevron open={open} className="text-sm" />}
      {/* Only the finished state is marked. */}
      <span className="text-xs text-emerald-600 w-2.5 shrink-0">
        {props.done ? "✓" : ""}
      </span>
      <h3 className="text-sm font-semibold text-slate-800">{props.title}</h3>
      {props.summary && (
        <span className="text-2xs text-slate-500 truncate">
          {props.summary}
        </span>
      )}
    </>
  );
  return (
    <section className="border border-slate-200 rounded-lg overflow-hidden bg-white">
      {collapsible ? (
        <div className={"flex items-stretch bg-slate-50 "
          + (open ? "border-b border-slate-200" : "")}>
          <button type="button" onClick={props.onToggle} aria-expanded={open}
            className={"flex-1 min-w-0 flex items-center gap-2 px-3 py-2.5 "
              + "text-left hover:bg-slate-100 transition-colors cursor-pointer"}>
            {heading}
          </button>
          {props.action && (
            <div className="flex items-center pr-3 pl-2">{props.action}</div>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2 px-3 py-2.5 bg-slate-50 border-b border-slate-200">
          {heading}
          {props.action && <div className="ml-auto">{props.action}</div>}
        </div>
      )}
      <Collapse open={open}>
        <div className="p-3">
          {props.hint && <p className="text-xs text-slate-500 mb-2">{props.hint}</p>}
          {props.children}
        </div>
      </Collapse>
    </section>
  );
}

interface SegmentOption {
  value: string;
  label: string;
  /** One line under the label, always visible. */
  hint?: string;
  /** Why the segment cannot be picked; it stays visible and says so. */
  disabledReason?: string;
}

/** An exclusive choice between two or three named alternatives, each worth a
 *  sentence. A Switch would imply one of them is "off". */
export function SegmentedControl(props: {
  value: string;
  onChange: (v: string) => void;
  options: SegmentOption[];
  label?: string;
}) {
  return (
    <div>
      {props.label && (
        <span className="text-xs font-medium text-slate-600">{props.label}</span>
      )}
      <div role="radiogroup" aria-label={props.label}
        className="mt-1 grid gap-2" style={{
          gridTemplateColumns: `repeat(${props.options.length}, minmax(0, 1fr))`,
        }}>
        {props.options.map((o) => {
          const on = o.value === props.value;
          const off = !!o.disabledReason;
          return (
            <button type="button" key={o.value} role="radio" aria-checked={on} disabled={off}
              title={o.disabledReason}
              onClick={() => props.onChange(o.value)}
              className={"text-left rounded-md border px-3 py-2 transition-colors " +
                (off
                  ? "border-slate-200 bg-slate-50 text-slate-400 cursor-not-allowed"
                  : on
                    ? "border-bzm bg-bzm/5 text-slate-900"
                    : "border-slate-300 text-slate-600 hover:bg-slate-50")}>
              <span className="flex items-center gap-1.5 text-sm font-medium">
                <span aria-hidden className={"inline-block w-3 h-3 rounded-full border " +
                  (on ? "border-[4px] border-bzm" : "border-slate-300")} />
                {o.label}
              </span>
              {(o.disabledReason ?? o.hint) && (
                <span className="block text-2xs leading-snug mt-0.5 pl-[18px]">
                  {o.disabledReason ?? o.hint}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

interface SelectOption {
  value: string | number;
  label: string;
}

/** The margin the open list keeps from the window's edge, and its least height. */
const LIST_EDGE = 8;
const LIST_MIN = 96;
/** Room enough below that the list does not flip upward. */
const LIST_COMFORTABLE = 224;

// Combobox with type-to-filter: shows the selected label; typing filters the
// list; ↑/↓ + Enter select, Esc/blur closes and restores the selection.
export function SearchSelect(props: {
  options: SelectOption[];
  value: string | number | null;
  onChange: (v: string | number) => void;
  /** Un-choose. Given, the clear button empties the selection once the typed
   *  search is gone; without it, it only clears the search. */
  onClear?: () => void;
  placeholder?: string;
  disabled?: boolean;
  /** The options are on their way; shown in the box. */
  busy?: boolean;
}) {
  const { options, value, onChange } = props;
  const selected = options.find((o) => o.value === value) ?? null;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [hi, setHi] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  useEffect(() => { setHi(0); }, [query, open]);
  useEffect(() => {
    listRef.current?.children[hi]?.scrollIntoView({ block: "nearest" });
  }, [hi]);

  // Where the list fits, measured on opening: it opens upward when there is
  // more room above, as at the foot of the nav drawer.
  const [drop, setDrop] = useState({ up: false, max: LIST_MIN });
  useEffect(() => {
    if (!open) return;
    const measure = () => {
      const r = rootRef.current?.getBoundingClientRect();
      if (!r) return;
      const below = window.innerHeight - r.bottom - LIST_EDGE;
      const above = r.top - LIST_EDGE;
      // Downward whenever there is a comfortable list's worth of room.
      const up = below < LIST_COMFORTABLE && above > below;
      // A ceiling only; a short list is as tall as its options.
      setDrop({ up, max: Math.max(LIST_MIN, up ? above : below) });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open]);

  // What the clear button clears: the search first, then the selection.
  const clearing: "query" | "selection" | null =
    (props.disabled || props.busy) ? null
      : query ? "query"
        : (selected && props.onClear) ? "selection" : null;

  const pick = (o: SelectOption) => {
    onChange(o.value);
    setOpen(false);
    setQuery("");
  };

  return (
    <div ref={rootRef} className="relative">
      <input
        ref={inputRef}
        className={inputCls + " pr-7"}
        disabled={props.disabled}
        placeholder={props.busy ? "loading…"
          : selected?.label ?? props.placeholder ?? "type to search…"}
        value={open ? query : selected?.label ?? ""}
        onFocus={() => { setOpen(true); setQuery(""); }}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "Enter")) { setOpen(true); return; }
          if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(h + 1, filtered.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
          else if (e.key === "Enter") { e.preventDefault(); if (filtered[hi]) pick(filtered[hi]); }
          else if (e.key === "Escape") { setOpen(false); setQuery(""); (e.target as HTMLInputElement).blur(); }
        }}
      />
      {/* Clear replaces the chevron whenever there is something to clear. On
          mousedown, prevented, so the input does not blur and close the list. */}
      {props.busy ? (
        <Spinner className="absolute right-2.5 top-1/2 -translate-y-1/2 text-bzm" />
      ) : clearing ? (
        <button type="button"
          aria-label={clearing === "query" ? "Clear search" : "Clear selection"}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-500
                     hover:text-slate-800 hover:bg-slate-200 rounded w-5 h-5
                     flex items-center justify-center text-xs leading-none"
          onMouseDown={(e) => {
            e.preventDefault();
            setQuery("");
            if (clearing === "selection") props.onClear?.();
            inputRef.current?.focus();
            setOpen(true);
          }}>
          ✕
        </button>
      ) : (
        <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 text-xs">▾</span>
      )}
      {open && (
        // z-40: above what it is nested in, still under a modal (z-50).
        <div ref={listRef}
          style={{ maxHeight: drop.max }}
          className={"absolute z-40 w-full overflow-y-auto bg-white border "
            + "border-slate-300 rounded-md shadow-lg "
            + (drop.up ? "bottom-full mb-1" : "mt-1")}>
          {filtered.map((o, i) => (
            <button key={o.value} type="button"
              className={`w-full text-left px-2.5 py-1.5 text-sm ${i === hi ? "bg-bzm/10 text-bzm-dark" : "hover:bg-slate-50"} ${o.value === value ? "font-semibold" : ""}`}
              onMouseEnter={() => setHi(i)}
              onMouseDown={(e) => { e.preventDefault(); pick(o); }}>
              {o.label}
            </button>
          ))}
          {filtered.length === 0 && (
            <p className="px-2.5 py-1.5 text-sm text-slate-400">no matches</p>
          )}
        </div>
      )}
    </div>
  );
}

/** A centred modal: dims the page, and Escape or a click outside closes it.
 *  Nothing is rendered while closed, so a dismissed form does not keep its input. */
export function Modal(props: {
  open: boolean;
  onClose: () => void;
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!props.open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") props.onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [props]);
  if (!props.open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={props.onClose}>
      <div className="absolute inset-0 bg-slate-900/40" />
      <div role="dialog" aria-modal="true" aria-label={props.title}
        onClick={(e) => e.stopPropagation()}
        className="relative bg-white rounded-xl shadow-2xl w-full max-w-xl
                   border border-slate-200">
        <div className="flex items-baseline gap-2 px-4 py-3 border-b border-slate-200">
          <h2 className="text-sm font-semibold text-slate-900">{props.title}</h2>
          {props.hint && (
            <span className="text-2xs text-slate-500 truncate">{props.hint}</span>
          )}
          <span className="grow" />
          <button type="button" onClick={props.onClose} aria-label="Close"
            className="text-slate-400 hover:text-slate-700 text-sm leading-none px-1">
            ✕
          </button>
        </div>
        <div className="p-4">{props.children}</div>
      </div>
    </div>
  );
}

/** A refusal or failure, in red. `className` replaces the default size and spacing. */
export function ErrorMsg({ msg, className = "text-xs mt-1.5" }: {
  msg: string | null; className?: string;
}) {
  if (!msg) return null;
  return <p className={"text-red-600 break-words " + className}>{msg}</p>;
}

/** Something to act on where the requested thing still happened, in amber
 *  rather than red so it does not invite a repeat click. */
export function NoticeMsg({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200
                       rounded-md px-2 py-1.5 mt-1.5 break-words">{msg}</p>;
}

const CALLOUT = {
  amber: "border-amber-200 bg-amber-50 text-amber-800",
  red: "border-red-300 bg-red-50 text-red-900",
  emerald: "border-emerald-200 bg-emerald-50 text-emerald-800",
};

/** An inline box of prose in one of three tones: amber to warn, red for what a
 *  click will destroy, emerald for what just succeeded. */
export function Callout(props: {
  tone: keyof typeof CALLOUT; children: ReactNode; className?: string;
}) {
  return (
    <div className={"rounded-md border px-3 py-2 " + CALLOUT[props.tone]
      + (props.className ? " " + props.className : "")}>
      {props.children}
    </div>
  );
}

/** The disclosure chevron: points right when closed and down when open. */
export function Chevron({ open, className = "text-xs" }: {
  open: boolean; className?: string;
}) {
  return (
    <span aria-hidden="true"
      className={"text-slate-400 leading-none shrink-0 transition-transform duration-150 "
        + (open ? "rotate-90 " : "") + className}>›</span>
  );
}

/** A body that folds open and shut under its header.
 *
 *  Kept mounted while closed so what was typed into it survives; `invisible`
 *  and aria-hidden while closed so its controls leave the tab order and stop
 *  taking clicks. Rows animate 0fr -> 1fr because `height: auto` does not. */
export function Collapse(props: { open: boolean; id?: string; children: ReactNode }) {
  return (
    <div id={props.id} aria-hidden={!props.open}
      className={"grid transition-[grid-template-rows] duration-[180ms] ease-out "
        + (props.open ? "grid-rows-[1fr]" : "grid-rows-[0fr] invisible")}>
      <div className="overflow-hidden">{props.children}</div>
    </div>
  );
}

/** A small header that shows or hides what is under it. The body is rendered
 *  only while open, so a closed one holds no fields. */
export function Disclosure(props: {
  open: boolean; onToggle: () => void; header: ReactNode; children: ReactNode;
  /** Classes for the header button. */
  className?: string;
}) {
  return (
    <>
      <button type="button" onClick={props.onToggle} aria-expanded={props.open}
        className={"flex items-center gap-1.5 text-left "
          + (props.className ?? "text-2xs text-slate-500 hover:text-slate-700")}>
        <Chevron open={props.open} />
        {props.header}
      </button>
      {props.open && props.children}
    </>
  );
}

/** A text field inside an editable row. Not inputCls: its w-full refuses to
 *  shrink in a flex row and pushes the rest of the row off the panel. */
export const rowFieldCls =
  "rounded-md border border-slate-300 px-2 py-1.5 text-xs bg-white " +
  "focus:outline-none focus:ring-2 focus:ring-bzm/40 focus:border-bzm";
export const rowInputCls = rowFieldCls + " flex-1 min-w-0";
export const rowSelectCls = rowFieldCls + " shrink-0";

/** Rows of fields with a remove button each and an add button under them.
 *
 *  `renderRow` draws one row's fields and writes a changed row with `edit`;
 *  `below` renders under a row, for its error. */
export function RowEditor<T>(props: {
  rows: T[];
  onChange: (rows: T[]) => void;
  blank: () => T;
  addLabel: string;
  removeLabel: (i: number) => string;
  renderRow: (row: T, i: number, edit: (row: T) => void) => ReactNode;
  below?: (row: T, i: number) => ReactNode;
}) {
  const { rows, onChange } = props;
  return (
    <div className="space-y-1.5">
      {rows.map((r, i) => (
        <div key={i}>
          <div className="flex items-center gap-1.5">
            {props.renderRow(r, i,
              (next) => onChange(rows.map((x, j) => (j === i ? next : x))))}
            <button type="button" title="Remove" aria-label={props.removeLabel(i)}
              className="text-slate-400 hover:text-red-600 text-sm px-1 shrink-0"
              onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
          </div>
          {props.below?.(r, i)}
        </div>
      ))}
      <button type="button" className="text-xs text-bzm hover:underline"
        onClick={() => onChange([...rows, props.blank()])}>
        {props.addLabel}
      </button>
    </div>
  );
}

