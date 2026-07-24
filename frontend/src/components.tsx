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

export function Field(props: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{props.label}</span>
      {props.children}
      {props.hint && <span className="text-[11px] text-slate-400">{props.hint}</span>}
    </label>
  );
}

export const inputCls =
  "mt-0.5 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm " +
  "focus:outline-none focus:ring-2 focus:ring-bzm/40 focus:border-bzm bg-white";

export function TextInput(props: {
  value: string; onChange: (v: string) => void; placeholder?: string; mono?: boolean;
}) {
  return (
    <input
      className={inputCls + (props.mono ? " font-mono text-xs" : "")}
      value={props.value}
      placeholder={props.placeholder}
      onChange={(e) => props.onChange(e.target.value)}
    />
  );
}

export function Check(props: {
  label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string;
}) {
  return (
    <label className="flex items-start gap-2 text-sm cursor-pointer select-none">
      <input
        type="checkbox"
        className="mt-0.5 accent-bzm"
        checked={props.checked}
        onChange={(e) => props.onChange(e.target.checked)}
      />
      <span>
        {props.label}
        {props.hint && <span className="block text-[11px] text-slate-400">{props.hint}</span>}
      </span>
    </label>
  );
}

export function Button(props: {
  onClick: () => void; children: ReactNode; kind?: "primary" | "ghost"; disabled?: boolean;
}) {
  const base = "rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40";
  const kinds = {
    primary: "bg-bzm text-white hover:bg-bzm-dark",
    ghost: "border border-slate-300 text-slate-600 hover:bg-slate-50",
  };
  return (
    <button className={`${base} ${kinds[props.kind ?? "primary"]}`}
      onClick={props.onClick} disabled={props.disabled}>
      {props.children}
    </button>
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
