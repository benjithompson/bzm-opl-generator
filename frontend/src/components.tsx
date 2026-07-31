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

/** The conventional red asterisk on a required field's label.
 *
 *  One definition because it appears on three forms now, and a marker that
 *  means "required" in two shapes means nothing in either. Not a state badge:
 *  it says the field must be filled in, which is true before anyone types and
 *  stays true afterwards. Whether it *has* been is the input's own border, and
 *  the disabled control below it that names what it is waiting for.
 *
 *  `aria-hidden` with the word beside it, because an asterisk is a convention
 *  for sighted readers and silence for everyone else. */
export function RequiredMark() {
  return (
    <>
      <span aria-hidden="true" className="text-red-600">*</span>
      <span className="sr-only">(required)</span>
    </>
  );
}

export function Field(props: {
  label: string; hint?: string; children: ReactNode;
  /** Marks the label with the asterisk. Whether the field is *filled in* is a
   *  different question and is not shown here -- see RequiredMark. */
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">
        {props.label}{props.required && <RequiredMark />}
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
/** One panel of a step: a bordered card with a header, optionally collapsible.
 *
 *  A card rather than a rule between blocks. Three sections separated by a
 *  hairline on one white background read as one long form with bold words in
 *  it -- where a panel starts and ends is the thing a reader needs before they
 *  need anything inside it, and a border is how that gets said.
 *
 *  When it collapses, the header follows the disclosure convention rather than
 *  inventing one: the whole bar is the control, the pointer changes over it, it
 *  tints on hover, and a chevron on the left points right when closed and down
 *  when open. A header that is clickable and does not look it is a header
 *  nobody clicks.
 */
export function SubSection(props: {
  title: string; hint?: string; done?: boolean; children: ReactNode;
  /** Collapsible when both are given. Controlled from the caller, because what
   *  should be open is a fact about where you are in the step -- the next
   *  unfinished thing -- and only the caller knows that. Given neither, the
   *  section is always open and has no header control. */
  open?: boolean;
  onToggle?: () => void;
  /** A word or two of state on the header, visible while collapsed: a folded
   *  section that says nothing is a section you have to open to find out
   *  whether you needed to. */
  summary?: string;
}) {
  const collapsible = props.open !== undefined && !!props.onToggle;
  const open = !collapsible || props.open;
  const heading = (
    <>
      {collapsible && (
        <span aria-hidden="true"
          className={"text-slate-400 text-sm leading-none transition-transform "
            + "duration-150 shrink-0 " + (open ? "rotate-90" : "")}>›</span>
      )}
      {/* Only the finished state is marked. An "unfinished" glyph on every step
          you have not reached yet reads as a list of failures. */}
      <span className="text-xs text-emerald-600 w-2.5 shrink-0">
        {props.done ? "✓" : ""}
      </span>
      <h3 className="text-sm font-semibold text-slate-800">{props.title}</h3>
      {props.summary && (
        <span className="text-[11px] text-slate-500 truncate">
          {props.summary}
        </span>
      )}
    </>
  );
  return (
    <section className="border border-slate-200 rounded-lg overflow-hidden bg-white">
      {collapsible ? (
        <button type="button" onClick={props.onToggle} aria-expanded={open}
          className={"w-full flex items-center gap-2 px-3 py-2.5 text-left "
            + "bg-slate-50 hover:bg-slate-100 transition-colors cursor-pointer "
            + (open ? "border-b border-slate-200" : "")}>
          {heading}
        </button>
      ) : (
        <div className="flex items-center gap-2 px-3 py-2.5 bg-slate-50 border-b border-slate-200">
          {heading}
        </div>
      )}
      {/* The same open/close as an agent row: grid-rows 0fr -> 1fr, because the
          body's height is not knowable in advance and `height: auto` does not
          transition. Kept mounted while closed so what was typed into it is
          still there when it reopens. */}
      {/* `invisible` as well as zero-height, and that is not decoration: the body
          stays mounted while closed so what was typed into it survives, and a
          mounted body inside a 0fr row is still in the hit-testing and
          accessibility trees. Its buttons took clicks aimed at whatever was
          drawn over them, and a keyboard tab walked into a section nobody could
          see. visibility:hidden takes it out of both while keeping the state. */}
      <div aria-hidden={!open}
        className={"grid transition-[grid-template-rows] duration-[180ms] ease-out "
          + (open ? "grid-rows-[1fr]" : "grid-rows-[0fr] invisible")}>
        <div className="overflow-hidden">
          <div className="p-3">
            {props.hint && <p className="text-xs text-slate-500 mb-2">{props.hint}</p>}
            {props.children}
          </div>
        </div>
      </div>
    </section>
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
  /** Un-choose. Given, the clear button empties the box itself once the typed
   *  search is gone -- because "clear the dropdown" means the value in it, and
   *  onChange has no way to say "none". Without it the button only clears the
   *  search, since a control that empties a field the page still depends on
   *  would be worse than no control. */
  onClear?: () => void;
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

  // What the button would clear, or null when the box is already empty. The
  // search wins while there is one: two presses to get from "typed a filter
  // over a chosen account" to "nothing chosen" is the order people expect,
  // and it makes the first press undoable.
  const clearing: "query" | "selection" | null =
    props.disabled ? null
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
      {/* Clear, in place of the chevron rather than beside it: while the list is
          open "this opens" is the one thing the arrow no longer has to say, and
          two glyphs crowd a box this size.

          There whenever there is something to clear, which is the fix for the
          first version of this: focusing the box empties it to show the full
          list, so an X gated on the typed search appeared only *after* a
          keystroke -- click in, and there was nothing there to find.

          Two things to clear, in order. The search first, if one has been
          typed; then the selection, if the caller gave us a way to un-choose.

          mousedown, and prevented: on click the input would blur first, and the
          list would close under the pointer that was clearing it. */}
      {clearing ? (
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
