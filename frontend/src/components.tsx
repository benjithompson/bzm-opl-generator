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

/** A whole-number field. Seven of these were `<input type="number">` with
 *  `inputCls` concatenated by hand, and they had already drifted -- one without
 *  a `min`, one with a width bolted onto the class string. The blank string is
 *  a legitimate value and means "not given"; see server._typed, which is the
 *  same fact on the other side of the wire. */
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

/** The white card everything on these pages sits in. Was six copies of the
 *  same class string across three files, one of which had already drifted to
 *  `space-y-2`. */
export const cardCls =
  "bg-white border border-slate-200 rounded-lg p-4 space-y-3";

/** One number out of a plan: the figure, what it counts, and what it costs.
 *
 *  Both places that size something show a row of these, and they had a copy
 *  each -- the same three lines at two type scales. `big` is the standalone
 *  planner, where the row is the answer to the whole page; the pane inside a
 *  location sits under four form fields and would shout over them. */
export function Figure(props: {
  n: number | string; unit: string; sub: string; big?: boolean;
}) {
  return (
    <div className={"border border-slate-200 rounded-md "
      + (props.big ? "p-3" : "px-2.5 py-2")}>
      <div className={"font-bold text-slate-900 leading-none "
        + (props.big ? "text-2xl" : "text-lg")}>{props.n}</div>
      <div className={"font-medium text-slate-600 "
        + (props.big ? "text-xs mt-1" : "text-[11px] mt-0.5")}>{props.unit}</div>
      <div className={"text-slate-400 "
        + (props.big ? "text-[11px] mt-0.5" : "text-[10px]")}>{props.sub}</div>
    </div>
  );
}

/** What a plan cannot know, and what it wants to warn about.
 *
 *  Both sizing panels showed this and had a copy each, in two wordings -- and
 *  the wording is the point: the users-per-engine figure is the number the
 *  whole plan multiplies by, nothing on this side can measure it, and a panel
 *  that softened the sentence would be the one people believed. `compact` is
 *  the pane inside a location, where it sits under a form rather than being
 *  the page.
 *
 *  The warnings themselves are plan.py's prose, rendered as it wrote them. */
export function PlanCaveats(props: {
  warnings: string[];
  compact?: boolean;
  /** Every model the plan was asked for. Each assumed figure gets its own note
   *  in its own unit — "4 browser instances per engine is assumed" is a
   *  different sentence about a different workload, and one note about virtual
   *  users standing in for both would be a figure attributed to the wrong
   *  thing.
   *
   *  Required, and one shape. There was a second — an `assumed`/`vusPerEngine`
   *  pair for a caller with only the performance figure — and both callers had
   *  the rows all along, so nothing ever reached it: with no plan `assumed` is
   *  false, which is the same empty list the rows give, and with one the rows
   *  are there. What it did carry was "virtual users per engine" hardcoded for
   *  whatever model the figure belonged to.
   *
   *  A model with **no** measured figure is not here: it has no note to make,
   *  because the sentence explaining it is the server's and arrives in
   *  `warnings` beside these. */
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
          <p className={small ? "text-[11px] text-amber-700" : "text-xs text-amber-900"}>
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
          <p className={small ? "text-[11px] text-slate-500" : "text-xs text-slate-600"}>
            {w}
          </p>
        </div>
      ))}
    </>
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

/** `label` names the control for a screen reader and for a test, since the
 *  visible title beside it is a sibling rather than a <label>. Optional: most
 *  switches here are a GroupRow's, whose row is the label. */
export function Switch({ on, onChange, label }: {
  on: boolean; onChange: (v: boolean) => void; label?: string;
}) {
  return (
    <button role="switch" aria-checked={on} aria-label={label}
      onClick={() => onChange(!on)}
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

/** The room the open list leaves at the window's edge, and the least it will
 *  make do with. The floor matters where nothing else does: a box pinned to the
 *  bottom of the window has no room below it and none above it either once the
 *  drawer is short, and two rows behind a scrollbar still beats none. */
const LIST_EDGE = 8;
const LIST_MIN = 96;
/** Room enough not to bother flipping the list to the other side -- about the
 *  fixed height it used to have, which was a comfortable list everywhere it had
 *  the space for one. */
const LIST_COMFORTABLE = 224;

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
  /** The options are on their way. Shown in the box rather than beside it: an
   *  empty dropdown and a slow one look identical, and the account and
   *  workspace lists are a round trip to BlazeMeter over whatever network the
   *  user is on. */
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

  // Where the list will fit, measured when it opens rather than assumed. This
  // control is used at the top of a form and at the *foot* of the nav drawer,
  // and a fixed height that suits the first runs off the bottom of the window
  // in the second -- which is where the workspace picker is, with 166 options
  // in it. So it takes the room that is actually there, and opens upward when
  // there is more above than below.
  const [drop, setDrop] = useState({ up: false, max: LIST_MIN });
  useEffect(() => {
    if (!open) return;
    const measure = () => {
      const r = rootRef.current?.getBoundingClientRect();
      if (!r) return;
      const below = window.innerHeight - r.bottom - LIST_EDGE;
      const above = r.top - LIST_EDGE;
      // Downward while there is a usable list's worth of room, whatever is
      // above: a list that flips sides because the window happens to be a
      // little taller upward is a control that moves for no reason the person
      // using it can see.
      const up = below < LIST_COMFORTABLE && above > below;
      // No upper bound: this is a ceiling, and a short list is still as tall as
      // its options. Capping it is how the picker came to show one row of a
      // list that had room for twenty.
      setDrop({ up, max: Math.max(LIST_MIN, up ? above : below) });
    };
    measure();
    // Resizing with the list open is rare; a zoom or a rotate that leaves it
    // hanging off the screen is the kind of thing nobody reports.
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open]);

  // What the button would clear, or null when the box is already empty. The
  // search wins while there is one: two presses to get from "typed a filter
  // over a chosen account" to "nothing chosen" is the order people expect,
  // and it makes the first press undoable.
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
        // z-40, not z-30: this list has to come out over whatever it is nested
        // in, and inside the account menu z-30 put it under the fields below
        // it. It is still under the modal layer (z-50), which is deliberate --
        // a dialog covers the page it was opened from.
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

/** A centred modal. Unlike the two drawers -- which are furniture and stay
 *  where they are put -- this one is a question being asked, so it dims what is
 *  behind it and both Escape and a click outside answer "not now".
 *
 *  Nothing is rendered while closed, because what it holds is a form whose
 *  half-typed contents should not survive being dismissed. */
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
            <span className="text-[11px] text-slate-500 truncate">{props.hint}</span>
          )}
          <span className="grow" />
          <button onClick={props.onClose} aria-label="Close"
            className="text-slate-400 hover:text-slate-700 text-sm leading-none px-1">
            ✕
          </button>
        </div>
        <div className="p-4">{props.children}</div>
      </div>
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

