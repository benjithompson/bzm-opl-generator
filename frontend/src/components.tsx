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

/** Whether a field that *must* be filled in has been.
 *
 *  A badge rather than an asterisk, and red rather than grey, because the rule
 *  it serves is that required input is obvious before anything is clicked: a
 *  disabled button explains nothing if the field it is waiting on looks the
 *  same as the four optional ones beside it. Lifted out of ConfigurePanel,
 *  which had the only copy, when the planner needed the same thing.
 *
 *  Reads "required" while empty rather than "missing": nothing has gone wrong
 *  yet on a form nobody has filled in. */
export function RequiredBadge({ ok }: { ok: boolean }) {
  const cls = "text-[10px] font-bold uppercase tracking-wide rounded px-1.5 py-0.5 ";
  return ok
    ? <span className={cls + "bg-emerald-100 text-emerald-700"}>✓ set</span>
    : <span className={cls + "bg-red-100 text-red-700"}>required</span>;
}

export function Field(props: {
  label: string; hint?: string; children: ReactNode;
  /** Omit for an optional field. Present means the field is required, and the
   *  value says whether it has been given -- so the badge is there from the
   *  first render rather than appearing once something is wrong. */
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
        {props.label}
        {props.required !== undefined && <RequiredBadge ok={props.required} />}
      </span>
      {props.children}
      {props.hint && <span className="text-[11px] text-slate-400">{props.hint}</span>}
    </label>
  );
}

export const inputCls =
  "mt-0.5 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm " +
  "focus:outline-none focus:ring-2 focus:ring-bzm/40 focus:border-bzm bg-white";

export function TextInput(props: {
  value: string; onChange: (v: string) => void; placeholder?: string;
  mono?: boolean;
  /** Shown, and not editable. Used where a field describes a state the page is
   *  already in -- the key it is connected with -- rather than an input waiting
   *  to be filled: hiding it would move everything below it. */
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

/** A credential in a form: masked, with a deliberate reveal.
 *
 *  #64 moved the AUTH_TOKEN out of the download and into a field -- captured when
 *  the agent is created, or pasted from what `create-ship` printed -- and that is
 *  the one place the change makes a token more visible than it was, since it now
 *  sits in the DOM instead of streaming into a zip. Masking is the mitigation: it
 *  is not secrecy (crane logs the token, and anyone who can read a pod log in
 *  that namespace can read the Secret) but permanence and reach, which is a
 *  screen share and a screenshot.
 *
 *  `type=password` rather than a CSS mask, so a password manager and a screen
 *  reader both understand what this is. */
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
  /** Shown but not changeable -- a box that describes a state rather than
   *  offering one. Hiding it instead would move whatever is below it. */
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
        {props.hint && <span className="block text-[11px] text-slate-400">{props.hint}</span>}
      </span>
    </label>
  );
}

/** Indeterminate progress, for a wait whose length we cannot predict -- a
 *  round-trip to BlazeMeter over someone's corporate network. `currentColor`
 *  so it works on both button kinds without being told which it is on. */
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
  /** Fill the width it is given, and centre the label in it. For a button
   *  whose label changes -- Connect / Connecting… / Disconnect -- in a row
   *  where a neighbour is `grow`: the caller fixes the width once and the
   *  label stops moving everything beside it. */
  block?: boolean;
  /** In flight: shows a spinner and stops a second click starting a second
   *  request. Separate from `disabled` so the caller does not have to conflate
   *  "not allowed" with "already going". */
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
    <button className={`${base} ${kinds[props.kind ?? "primary"]}`}
      onClick={props.onClick} disabled={props.disabled || props.busy}
      aria-busy={props.busy || undefined}>
      {props.busy && <Spinner />}
      {props.children}
    </button>
  );
}

export function Switch({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button role="switch" aria-checked={on} onClick={() => onChange(!on)}
      className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${on ? "bg-bzm" : "bg-slate-300"}`}>
      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

/** A step within a numbered Section -- same heading shape, no number of its
 *  own. Used where several former steps were folded into one. */
export function SubSection(props: {
  title: string; hint?: string; done?: boolean; children: ReactNode;
}) {
  return (
    <div className="border-t border-slate-100 pt-3 first:border-t-0 first:pt-0">
      <div className="flex items-baseline gap-2 mb-2">
        {/* Only the finished state is marked. An "unfinished" glyph on every
            step you have not reached yet reads as a list of failures. The span
            keeps its width either way so the headings stay aligned. */}
        <span className="text-xs text-emerald-600 w-2.5 shrink-0">
          {props.done ? "✓" : ""}
        </span>
        <h3 className="text-sm font-semibold text-slate-800">{props.title}</h3>
      </div>
      {props.hint && <p className="text-xs text-slate-500 mb-2">{props.hint}</p>}
      {props.children}
    </div>
  );
}

export interface SegmentOption {
  value: string;
  label: string;
  /** One line under the label, always visible -- these are choices someone
   *  makes once and needs to understand, not toggles they flip while reading. */
  hint?: string;
  /** Set to explain why the segment cannot be picked. A disabled segment stays
   *  visible and says why: hiding it would leave "where did the Helm option go"
   *  as the user's problem to solve. */
  disabledReason?: string;
}

/** An exclusive choice between two or three named alternatives, where both are
 *  legitimate and the difference is worth a sentence. A Switch would imply one
 *  of them is "off". */
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
            <button key={o.value} role="radio" aria-checked={on} disabled={off}
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
                <span className="block text-[11px] leading-snug mt-0.5 pl-[18px]">
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

export interface SelectOption {
  value: string | number;
  label: string;
}

// Combobox with type-to-filter: shows the selected label; typing filters the
// list; ↑/↓ + Enter select, Esc/blur closes and restores the selection.
export function SearchSelect(props: {
  options: SelectOption[];
  value: string | number | null;
  onChange: (v: string | number) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const { options, value, onChange } = props;
  const selected = options.find((o) => o.value === value) ?? null;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [hi, setHi] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

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

  const pick = (o: SelectOption) => {
    onChange(o.value);
    setOpen(false);
    setQuery("");
  };

  return (
    <div ref={rootRef} className="relative">
      <input
        className={inputCls + " pr-7"}
        disabled={props.disabled}
        placeholder={selected?.label ?? props.placeholder ?? "type to search…"}
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
      <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 text-xs">▾</span>
      {open && (
        <div ref={listRef}
          className="absolute z-30 mt-1 w-full max-h-56 overflow-y-auto bg-white border border-slate-300 rounded-md shadow-lg">
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

export function ErrorMsg({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <p className="text-xs text-red-600 mt-1.5 break-words">{msg}</p>;
}

/** Something the operator has to act on, where the thing they asked for still
 *  happened. Distinct from ErrorMsg on purpose: an agent that was created but
 *  whose credential the account refused to issue is not a failed creation, and
 *  showing it in red invites a second click that makes a second agent. */
export function NoticeMsg({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200
                       rounded-md px-2 py-1.5 mt-1.5 break-words">{msg}</p>;
}

export function JsonArea(props: {
  label: string; value: unknown; placeholder: string;
  onValid: (v: unknown) => void; rows?: number;
}) {
  const [text, setText] = useState(props.value ? JSON.stringify(props.value, null, 1) : "");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Field label={props.label}>
      <textarea
        className={inputCls + " font-mono text-xs"}
        rows={props.rows ?? 3}
        value={text}
        placeholder={props.placeholder}
        onChange={(e) => {
          const t = e.target.value;
          setText(t);
          if (!t.trim()) { setErr(null); props.onValid(null); return; }
          try { props.onValid(JSON.parse(t)); setErr(null); }
          catch { setErr("invalid JSON"); }
        }}
      />
      <ErrorMsg msg={err} />
    </Field>
  );
}
