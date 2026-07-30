// THROWAWAY. Floating variant switcher for the configure-step prototype.
// Delete with the rest of src/prototype/ once a layout is picked.

const KEYS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"] as const;
export type VariantKey = (typeof KEYS)[number];

export const VARIANT_NAMES: Record<VariantKey, string> = {
  A: "Shared deck + feature cards",
  B: "Status rail + one long form",
  C: "One list, features as parent rows",
  // D-G are all A+rail, full width; they differ in where the preview lives.
  D: "A+rail · preview in a bottom drawer",
  E: "A+rail · preview inline at the end",
  F: "A+rail · preview slides over the right",
  G: "A+rail · Configure / Preview tabs",
  // H-J keep G (the chosen preview placement) and change the steps instead:
  // one panel open at a time, controls that never move.
  H: "G · one step, collapsed neighbours, footer controls",
  I: "G · one step, top stepper, controls in the bar",
  J: "G · one step, arrows at the middle of each edge",
};

/** Which preview placement a variant uses -- itself for D-G, and G for the
 *  step-flow variants built on top of it. Null keeps the shipped two-column
 *  page. Shared with App, which drops its grid whenever this is set. */
export type ShellKey = "D" | "E" | "F" | "G";
export function shellFor(v: VariantKey | null): ShellKey | null {
  if (!v) return null;
  if ("DEFG".includes(v)) return v as ShellKey;
  return "HIJ".includes(v) ? "G" : null;
}

/** Which step flow, if any. A-G leave the three panels stacked. */
export type StepKey = "H" | "I" | "J";
export const stepFor = (v: VariantKey | null): StepKey | null =>
  v && "HIJ".includes(v) ? (v as StepKey) : null;

/** Which variant the URL asks for, or null for the shipped layout. Read from
 *  the location rather than state: there is no router in this app, and a
 *  reload-stable ?variant= is the whole point. */
export function variantFromUrl(): VariantKey | null {
  const v = new URLSearchParams(window.location.search).get("variant");
  return (KEYS as readonly string[]).includes(v ?? "") ? (v as VariantKey) : null;
}

function go(v: string | null) {
  const u = new URL(window.location.href);
  if (v) u.searchParams.set("variant", v);
  else u.searchParams.delete("variant");
  window.location.href = u.toString();
}

export function PrototypeSwitcher({ current }: { current: VariantKey | null }) {
  // Never in a built bundle: a stray merge cannot ship the bar to a user.
  // Cast because vite/client types are not in this tsconfig.
  if ((import.meta as unknown as { env?: { PROD?: boolean } }).env?.PROD) return null;
  // null (shipped) sits in the ring too, so the current layout is one arrow
  // press away from any variant.
  const ring: (VariantKey | null)[] = [null, ...KEYS];
  const i = ring.indexOf(current);
  const step = (d: number) => go(ring[(i + d + ring.length) % ring.length]);
  return (
    // Top-right: D pins a drawer along the bottom edge and F a button to the
    // bottom right, and a switcher sitting on top of the thing being judged is
    // no use.
    <div className="fixed top-2 right-3 z-50 flex items-center gap-1 rounded-full bg-slate-900/95 text-white shadow-lg shadow-slate-900/30 px-2 py-1.5 text-xs font-medium">
      <button className="px-2 py-0.5 rounded-full hover:bg-white/15" onClick={() => step(-1)}>←</button>
      <span className="px-2">
        {current ? `${current} — ${VARIANT_NAMES[current]}` : "shipped layout"}
      </span>
      <button className="px-2 py-0.5 rounded-full hover:bg-white/15" onClick={() => step(1)}>→</button>
      <span className="ml-1 text-[10px] uppercase tracking-wide text-white/40">prototype</span>
    </div>
  );
}
