// Step 2: what goes in the bundle.
//
// The option groups split two ways and the page says which is which. Most of
// them belong to no feature -- registry, proxy, CA trust, scheduling, security
// -- and are here whatever the location runs; the rest belong to one, and live
// in that feature's own card. There used to be a feature *selector* switching
// between two views of the same six groups, so pressing it changed a single row
// while reading as though it changed the step. Nothing is hidden now, so
// nothing has to be recovered: no "also in this bundle", no "not in view".
//
// The rail is orientation, not navigation-with-a-dot: it names what is set, so
// "what is in this bundle" is answered without scrolling the form.
import { ReactNode, useState } from "react";
import { Feature, Options } from "../api";
import { Button, Check, Field, inputCls, Switch } from "../components";
import { GroupRow } from "../groups/GroupRow";
import {
  GroupFlags, GroupId, groupsOf, OptionGroup, SHARED_GROUPS,
} from "../optionGroups";

export interface ConfigurePanelProps {
  features: Feature[];
  /** Manual mode's declaration of what the location runs. Connected it is read
   *  off the account and this is only which card is highlighted. */
  feature: string | null;
  pickFeature: (id: string) => void;
  sourceMode: "connect" | "manual";
  /** What the location's funcIds say it runs, and the funcIds no feature
   *  claims. Empty in manual mode: there is no account to have read. */
  locFeatures: string[];
  locUnclaimed: string[];
  /** Features the location does not run. Not "unavailable": they can be
   *  switched on here, which is what the card asks about. */
  notEnabled: string[];
  /** Turn a feature on for this location. Returns once the account has it. */
  enableFeature: (id: string) => Promise<void>;
  options: Options;
  set: (k: string, v: unknown) => void;
  grpOn: GroupFlags;
  grpRequired: Partial<GroupFlags>;
  grpDeclined: Partial<GroupFlags>;
  flipGroup: (id: GroupId, on: boolean) => void;
  groupBody: Record<GroupId, ReactNode>;
  /** Groups in use but unfinished, so the download is blocked. */
  incomplete: OptionGroup[];
  namespaceOk: boolean;
  saOk: boolean;
  saCreate: boolean;
  exportProfile: () => void;
  importProfile: (f: File) => void;
}

function rows(p: ConfigurePanelProps, gs: OptionGroup[]) {
  return gs.map((g) => (
    <GroupRow key={g.id} group={g} on={p.grpOn[g.id]}
      required={!!p.grpRequired[g.id]} declined={!!p.grpDeclined[g.id]}
      applies="" onFlip={(v) => p.flipGroup(g.id, v)}>
      {p.groupBody[g.id]}
    </GroupRow>
  ));
}

/** Namespace and service account. Not behind a switch like everything else
 *  here: every deployment has both, and putting the required half of a pair
 *  behind a toggle makes it look optional. */
function CoreFields(p: ConfigurePanelProps) {
  const ok = (good: boolean) => good
    ? <span className="text-[10px] font-bold uppercase tracking-wide bg-emerald-100 text-emerald-700 rounded px-1.5 py-0.5">✓ set</span>
    : <span className="text-[10px] font-bold uppercase tracking-wide bg-red-100 text-red-700 rounded px-1.5 py-0.5">required</span>;
  return (
    <div className="space-y-3">
      <label className="block">
        <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
          Namespace {ok(p.namespaceOk)}
        </span>
        <input className={inputCls + (p.namespaceOk ? " border-emerald-400" : " border-red-300")}
          value={String(p.options.namespace ?? "")} placeholder="e.g. blazemeter"
          onChange={(e) => p.set("namespace", e.target.value)} />
      </label>
      <div className="grid grid-cols-[1fr_auto] gap-4 items-start">
        <label className="block">
          <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
            Service account {ok(p.saOk)}
          </span>
          <input className={inputCls + (p.saOk ? "" : " border-red-300")}
            value={String(p.options.service_account_name ?? "")}
            placeholder="e.g. crane"
            onChange={(e) => p.set("service_account_name", e.target.value)} />
          <span className="text-[11px] text-slate-400">
            what the agent runs as, and what the RoleBinding grants to
          </span>
        </label>
        <div className="pt-5 w-52">
          <Check label="Create it"
            hint={p.saCreate ? "the bundle includes the ServiceAccount"
              : "referenced, not created — a wrong name leaves the agent pod unscheduled"}
            checked={p.saCreate}
            onChange={(v) => p.set("service_account_create", v)} />
        </div>
      </div>
    </div>
  );
}

// -- crane-hook --------------------------------------------------------------
// github.com/Blazemeter/crane-hook: BlazeMeter's own cluster-readiness checker,
// a one-shot Pod plus its own Role and RoleBinding. Every variable it takes --
// namespace, the RBAC names, the expose type and its TLS secret, the registry,
// the proxy -- is something this page already decides, which is why it is a
// switch here rather than a page in a doc. It is not a group: groups are
// declared in optionGroups.ts and own option keys that shape the agent, and
// this shapes nothing about the agent -- it adds a check that runs beside it.
function CraneHookRow(p: ConfigurePanelProps) {
  const on = !!p.options.crane_hook;
  return (
    <div className="px-3 py-2.5">
      <div className="flex items-center gap-3">
        <Switch on={on} onChange={(v) => p.set("crane_hook", v || null)} />
        <div className="min-w-0 grow">
          <p className={`text-sm font-medium ${on ? "text-slate-900" : "text-slate-500"}`}>
            Cluster check (crane-hook)
          </p>
          <p className="text-[11px] text-slate-400 truncate">
            a one-shot Pod that checks capacity, egress, RBAC and ingress before the agent runs
          </p>
        </div>
      </div>
      {on && (
        <p className="mt-2 pl-12 text-[11px] text-slate-500">
          Adds <span className="font-mono">bzm_cranehook.yaml</span> — a Pod, a
          Role and a RoleBinding, configured from the settings above. It runs
          once and exits 0 or 1; delete it when it has. Under Helm it is the
          chart&apos;s own <span className="font-mono">helm test</span> hook.
        </p>
      )}
    </div>
  );
}

/** Advanced, as a row in the settings list rather than a dashed box under it.
 *  It is two fields with the same weight as any other pair here; what makes it
 *  advanced is that it is closed, not that it sits outside the form. */
function AdvancedRow(p: ConfigurePanelProps) {
  const [open, setOpen] = useState(false);
  const openshift = p.options.platform === "openshift";
  return (
    <div className="px-3 py-2.5">
      <button className="w-full flex items-center gap-3 text-left"
        onClick={() => setOpen(!open)}>
        <span className="text-slate-400 text-xs w-3">{open ? "▾" : "▸"}</span>
        <span className="min-w-0 grow">
          <span className="block text-sm font-medium text-slate-500">Advanced</span>
          <span className="block text-[11px] text-slate-400">
            security posture and UID — you should not need these
          </span>
        </span>
      </button>
      {open && (
        <div className="mt-3 pl-6 grid grid-cols-2 gap-3">
          <Field label="Security posture"
            hint="SCC-friendly works on OpenShift and vanilla k8s; the pinned-UID variant is only for clusters that reject it">
            <select className={inputCls} value={String(p.options.platform)}
              onChange={(e) => p.set("platform", e.target.value)}>
              <option value="openshift">Unified SCC-friendly (recommended)</option>
              <option value="k8s">Legacy pinned-UID k8s</option>
            </select>
          </Field>
          {!openshift && (
            <Field label="runAsUser / runAsGroup">
              <input type="number" className={inputCls}
                value={Number(p.options.run_as_user ?? 1337)}
                onChange={(e) => p.set("run_as_user", Number(e.target.value))} />
            </Field>
          )}
        </div>
      )}
    </div>
  );
}

/** One feature: whether it is on, and the options it owns. */
function FeatureCard(p: ConfigurePanelProps & { feat: Feature }) {
  const { feat } = p;
  const manual = p.sourceMode === "manual";
  const own = groupsOf(feat.id);
  // Enabled means the location runs it -- or, in manual mode, that this is what
  // the typed identity was declared to be.
  const on = manual ? p.feature === feat.id : !p.notEnabled.includes(feat.id);
  // Before the account has been read there is nothing to say: `notEnabled` is
  // empty then, and claiming "enabled" from an unanswered question is the
  // collapse this codebase keeps refusing to make.
  const known = manual || p.locFeatures.length > 0;
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const enable = async () => {
    setBusy(true);
    try {
      await p.enableFeature(feat.id);
      setAsking(false);
      // The pane is the group's own body, so turning the group on is what
      // expands it -- and its enable() seeds whatever the group needs (SV lands
      // on nginx rather than on no backend at all).
      const first = own[0];
      if (first && !p.grpOn[first.id]) p.flipGroup(first.id, true);
    } finally { setBusy(false); }
  };
  return (
    <div id={"cfg-f-" + feat.id}
      className={"scroll-mt-4 rounded-xl border " + (on
        ? "border-bzm/40 bg-bzm/[0.03]" : "border-slate-200 bg-slate-50/70")}>
      <div className="px-3 py-2.5 border-b border-slate-100">
        {/* State first: whether the feature is on is what the card is about.
            Manual mode has no account to read the answer off, so there it is
            the control rather than a chip. */}
        {manual ? (
          <label className="flex items-center gap-2 text-[11px] font-medium text-slate-600 mb-1">
            <input type="radio" checked={on}
              onChange={() => p.pickFeature(feat.id)} />
            Enabled
          </label>
        ) : known && (
          <span className={"inline-block mb-1 text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 "
            + (on ? "bg-emerald-100 text-emerald-700" : "bg-slate-200 text-slate-500")}>
            {on ? "Enabled" : "Not enabled"}
          </span>
        )}
        <p className={"text-sm font-medium " + (on ? "text-slate-900" : "text-slate-500")}>
          {feat.label}
        </p>
        <p className="text-[11px] text-slate-400">{feat.hint}</p>
      </div>

      {/* Off on this location, so its switches are inert until the question
          under them is answered. The click still lands -- it is what asks --
          but nothing is written by it. */}
      {!on && !manual && known && (
        asking ? (
          <div className="px-3 py-2.5 border-b border-slate-100 bg-white">
            <p className="text-xs text-slate-700">
              <b>{feat.label}</b> is not enabled on this location. Enable it and
              configure it here?
            </p>
            <p className="text-[11px] text-slate-500 mt-0.5">
              Adds <span className="font-mono">{feat.func_ids[0]}</span> to the
              location in BlazeMeter — the agent is only asked to serve what the
              location says it runs.
            </p>
            <div className="flex gap-2 mt-2">
              <Button onClick={enable} busy={busy}>Enable on location</Button>
              {!busy && (
                <Button kind="ghost" onClick={() => setAsking(false)}>Cancel</Button>
              )}
            </div>
          </div>
        ) : (
          <button className="w-full text-left px-3 py-1.5 text-[11px] text-bzm hover:underline"
            onClick={() => setAsking(true)}>
            Enable on this location…
          </button>
        )
      )}

      <div className={!on && !manual && known ? "opacity-60 pointer-events-none select-none" : ""}
        onClickCapture={!on && !manual && known
          ? (e) => { e.preventDefault(); setAsking(true); } : undefined}>
        {own.length
          ? <div className="divide-y divide-slate-100">{rows(p, own)}</div>
          : <p className="px-3 py-3 text-[11px] text-slate-400">
              nothing extra to configure — it uses the settings above
            </p>}
      </div>
    </div>
  );
}

export function ConfigurePanel(p: ConfigurePanelProps) {
  const secs = [
    ...p.features.map((f) => ({
      id: "f-" + f.id, label: f.label, gs: groupsOf(f.id), anchor: "cfg-f-" + f.id,
    })),
    { id: "core", label: "Placement", gs: [] as OptionGroup[], anchor: "cfg-core" },
    { id: "shared", label: "Configure agent", gs: SHARED_GROUPS, anchor: "cfg-shared" },
  ];
  return (
    <div className="space-y-4">
      <div className="flex gap-2 items-center flex-wrap">
        <span className="flex-1" />
        <Button kind="ghost" onClick={p.exportProfile}>Export</Button>
        <label className="rounded-md px-3 py-1.5 text-sm font-medium border border-slate-300 text-slate-600 hover:bg-slate-50 cursor-pointer">
          Import
          <input type="file" accept=".json" className="hidden"
            onChange={(e) => e.target.files?.[0] && p.importProfile(e.target.files[0])} />
        </label>
      </div>

      <div className="grid grid-cols-[13rem_1fr] gap-6 items-start">
        <nav className="sticky top-4 space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 px-2">
            In this bundle
          </p>
          {secs.map((s) => {
            // The switches, not detect(): a group turned on and not yet filled
            // in has to appear here or the rail contradicts the form beside it.
            const set = s.gs.filter((g) => p.grpOn[g.id]);
            const todo = s.id === "core"
              ? !(p.namespaceOk && p.saOk)
              : s.gs.some((g) => p.incomplete.includes(g));
            const detail = todo ? "needs attention"
              : s.id === "core" ? String(p.options.namespace ?? "")
              : set.length ? set.map((g) => g.title).join(", ")
              : "defaults";
            return (
              <a key={s.id} href={"#" + s.anchor}
                className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50">
                <span className={"mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full "
                  + (todo ? "bg-red-500"
                    : set.length || s.id === "core" ? "bg-emerald-500"
                    : "bg-slate-300")} />
                <span className="min-w-0">
                  <span className="block text-xs font-medium text-slate-700">
                    {s.label}
                  </span>
                  <span className={"block text-[10px] "
                    + (todo ? "text-red-600" : "text-slate-400")}>
                    {detail}
                  </span>
                </span>
              </a>
            );
          })}
        </nav>

        <div className="min-w-0 space-y-5">
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Deployment features
            </h3>
            {/* Stacked, not side by side: a card holds group rows, and two
                columns of those read as two cramped tables. */}
            <div className="space-y-3">
              {p.features.map((f) => (
                <FeatureCard key={f.id} {...p} feat={f} />
              ))}
            </div>
            {p.locUnclaimed.length > 0 && (
              <p className="text-[11px] text-slate-500 mt-1.5">
                Also runs{" "}
                <span className="font-mono">{p.locUnclaimed.join(", ")}</span> —
                no options here for those; nothing about them is generated or
                removed.
              </p>
            )}
          </section>

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Deployment settings
            </h3>
            <div className="rounded-xl border border-slate-200 p-3 space-y-3">
              <div id="cfg-core" className="scroll-mt-4"><CoreFields {...p} /></div>
              <div id="cfg-shared"
                className="scroll-mt-4 border border-slate-200 rounded-lg divide-y divide-slate-100">
                {rows(p, SHARED_GROUPS)}
                <CraneHookRow {...p} />
                <AdvancedRow {...p} />
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
