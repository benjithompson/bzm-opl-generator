import { ReactNode, useState } from "react";

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
