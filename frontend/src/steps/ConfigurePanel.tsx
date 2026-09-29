// Step 2: what goes in the bundle. The output format comes first because it
// decides which other fields exist (formats.optionApplies over the served
// IGNORED_BY_FORMAT). Shared groups sit in Agent settings; a functionality's own
// groups sit in its card, shown only for what the location runs. The rail names
// what is set.
import { ReactNode, useState } from "react";
import { Functionality, Options } from "../api";
import {
  Button, Callout, Check, Chevron, Field, inputCls, SegmentedControl,
} from "../components";
import { envToRows } from "../env";
import { Applies, keysApply, OUTPUT_FORMATS } from "../formats";
import { GroupRow } from "../groups/GroupRow";
import {
  engineFunctionalities, GroupFlags, GroupId, groupsFor, groupsOf, OptionGroup,
  runsFunctionality, SHARED_GROUPS, UnclaimedFuncIds,
} from "../optionGroups";
import { marker, placeholderWarning } from "../placeholder";
// Crane's one pod-limit pair: stated as a rule where a location is being
// declared, as a warning where it already exists.
import { SV_ALONE, SV_MIXED, svMixedWithEngines } from "../sv";
import { plural } from "../text";

interface ConfigurePanelProps {
  functionalities: Functionality[];
  /** Manual entry's declaration, one box at a time. Only manual entry calls it. */
  declare: (id: string, on: boolean) => void;
  sourceMode: "connect" | "manual";
  /** The location's funcIds no card claims: served but unconfigured, or retired. */
  locUnclaimed: UnclaimedFuncIds;
  /** Which functionalities this location runs, or null while nobody has said
   *  (optionGroups.enabledFunctionalities). */
  enabled: string[] | null;
  options: Options;
  set: (k: string, v: unknown) => void;
  /** The output format, first on the step because it decides what the rest asks. */
  format: string;
  setFormat: (v: string) => void;
  /** Does this option reach anything in a bundle of this format? Everything
   *  below hides by it. */
  applies: Applies;
  grpOn: GroupFlags;
  grpRequired: Partial<GroupFlags>;
  grpDeclined: Partial<GroupFlags>;
  /** The engine size as prose; it is set on the location, so nothing here
   *  edits it. Null where the format has no limits (docker). */
  engineNote: string | null;
  flipGroup: (id: GroupId, on: boolean) => void;
  groupBody: Record<GroupId, ReactNode>;
  /** The environment variables area, assembled in App. Not a group. */
  envArea: ReactNode;
  /** Groups in use but unfinished; some block the step, some only say so. */
  incomplete: OptionGroup[];
  /** Required fields left empty, which the bundle carries as `<KEY>`. Warned
   *  about, never blocking. */
  blanks: string[];
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

/** Namespace and service account: not behind a switch, since every cluster
 *  deployment has both. Its own card because a docker bundle has neither. */
function CoreFields(p: ConfigurePanelProps) {
  // A blank field is allowed (it becomes a marker), so it is amber, not red,
  // and has no asterisk. Each hint names the marker the field becomes.
  const blankCls = (ok: boolean) => inputCls + (ok ? "" : " border-amber-300");
  return (
    <div className="space-y-3">
      <label className="block">
        <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
          Namespace
        </span>
        {/* The placeholder is a sample here; the hint names the marker. */}
        <input className={blankCls(p.namespaceOk)}
          value={String(p.options.namespace ?? "")} placeholder="e.g. blazemeter"
          onChange={(e) => p.set("namespace", e.target.value)} />
        <span className="text-2xs text-slate-400">
          every object in the bundle is created in it — left empty, the bundle
          carries {marker("namespace")} and cannot be applied
        </span>
      </label>
      <div className="grid grid-cols-[1fr_auto] gap-4 items-start">
        <label className="block">
          <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
            Service account
          </span>
          <input className={blankCls(p.saOk)}
            value={String(p.options.service_account_name ?? "")}
            placeholder="e.g. crane"
            onChange={(e) => p.set("service_account_name", e.target.value)} />
          <span className="text-2xs text-slate-400">
            what the agent runs as, and what the RoleBinding grants to — left
            empty, the bundle carries {marker("service_account_name")} rather
            than falling back to the namespace’s <code>default</code>
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

/** A settings row with no switch: a title, a hint, and a body closed until
 *  opened. For sections that have nothing to switch off. */
function FoldRow(props: {
  title: string; hint: string; children: ReactNode;
  /** A word or two visible while closed. */
  summary?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="px-3 py-2.5">
      <button type="button" className="w-full flex items-center gap-3 text-left"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <Chevron open={open} className="text-xs w-3 text-center" />
        <span className="min-w-0 grow">
          <span className="block text-sm font-medium text-slate-500">
            {props.title}
            {props.summary && (
              <span className="ml-2 text-2xs font-normal text-bzm">
                {props.summary}
              </span>
            )}
          </span>
          <span className="block text-2xs text-slate-400">{props.hint}</span>
        </span>
      </button>
      {open && <div className="mt-3 pl-6">{props.children}</div>}
    </div>
  );
}

/** Advanced: the security posture and, under the SCC-friendly one, which
 *  cluster the instructions are written for (the posture alone cannot say, since
 *  vanilla Kubernetes may use it too). */
function AdvancedRow(p: ConfigurePanelProps) {
  const posture = p.options.platform === "openshift";
  // Absent shows as OpenShift, and a select rather than a checkbox shows that.
  const openshift = p.options.openshift_cluster !== false;
  return (
    <FoldRow title="Advanced"
      hint="security posture, cluster and UID — you should not need these">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Security posture"
          hint="SCC-friendly works on OpenShift and vanilla k8s; the pinned-UID variant is only for clusters that reject it">
          <select className={inputCls} value={String(p.options.platform)}
            onChange={(e) => p.set("platform", e.target.value)}>
            <option value="openshift">Unified SCC-friendly (recommended)</option>
            <option value="k8s">Legacy pinned-UID k8s</option>
          </select>
        </Field>
        {posture && p.applies("openshift_cluster") && (
          <Field label="Cluster"
            hint="which commands the bundle's instructions are written in, and whether OpenShift-only options are offered">
            <select className={inputCls} value={openshift ? "openshift" : "k8s"}
              onChange={(e) => {
                const on = e.target.value === "openshift";
                p.set("openshift_cluster", on);
                // Injection off OpenShift fills nothing, so clear it with the
                // control that set it.
                if (!on) p.set("ca_openshift_inject", false);
              }}>
              <option value="openshift">OpenShift — oc</option>
              <option value="k8s">Plain Kubernetes — kubectl</option>
            </select>
          </Field>
        )}
        {!posture && (
          <Field label="runAsUser / runAsGroup">
            <input type="number" className={inputCls}
              value={Number(p.options.run_as_user ?? 1337)}
              onChange={(e) => p.set("run_as_user", Number(e.target.value))} />
          </Field>
        )}
      </div>
    </FoldRow>
  );
}

/** One functionality: its state and, where it runs, the options it owns. In
 *  connect mode only cards the location runs reach here, so `!on` means manual
 *  entry, where the checkbox is the declaration. */
function FunctionalityCard(
    p: ConfigurePanelProps & {
      feat: Functionality; own: OptionGroup[];
      /** Does the engine-size statement belong on this card? Decided by the
       *  panel so it renders once. */
      statesEngineSize: boolean;
    }) {
  const { feat, own } = p;
  // The statement renders under a functionality whose agent runs an engine.
  const note = p.statesEngineSize ? p.engineNote : null;
  const manual = p.sourceMode === "manual";
  // Declared (manual) or run (connected); unanswered reads as on.
  const on = runsFunctionality(p.enabled, feat.id);
  // Before the account answers there is no state to show.
  const known = p.enabled != null;
  return (
    <div id={"cfg-f-" + feat.id}
      className={"scroll-mt-4 rounded-xl border " + (on
        ? "border-bzm/40 bg-bzm/[0.03]" : "border-slate-200 bg-slate-50/70")}>
      <div className="px-3 py-2.5 border-b border-slate-100">
        {/* Manual entry has no account to read, so the state is the control. */}
        {manual ? (
          <label className="flex items-center gap-2 text-2xs font-medium text-slate-600 mb-1">
            {/* A checkbox, not a radio: a location runs several functionalities.
                Ticking one suggests its namespace, as picking a location does;
                a typed namespace still wins. */}
            <input type="checkbox" checked={on}
              onChange={(e) => p.declare(feat.id, e.target.checked)} />
            Enabled
          </label>
        ) : known && (
          <span className={"inline-block mb-1 text-3xs font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 "
            + (on ? "bg-emerald-100 text-emerald-700" : "bg-slate-200 text-slate-500")}>
            {on ? "Enabled" : "Not enabled"}
          </span>
        )}
        <p className={"text-sm font-medium " + (on ? "text-slate-900" : "text-slate-500")}>
          {feat.label}
        </p>
        <p className="text-2xs text-slate-400">{feat.hint}</p>
      </div>

      {/* Not declared (manual entry), nothing of its own, or its rows. */}
      {!on ? (
        <p className="px-3 py-3 text-2xs text-slate-500">
          Not what this identity was declared to run — tick <b>Enabled</b> above
          to configure it.
        </p>
      ) : own.length || note ? (
        <div className="divide-y divide-slate-100">
          {rows(p, own)}
          {note && (
            <p className="px-3 py-3 text-2xs text-slate-500">
              <span className="font-medium text-slate-700">Engine size.</span>{" "}
              {note}
            </p>
          )}
        </div>
      ) : (
        <p className="px-3 py-3 text-2xs text-slate-400">
          nothing extra to configure — it uses the settings above
        </p>
      )}
    </div>
  );
}

/** The option keys the placement card and Advanced own. Neither is a declared
 *  group, so each hides by its keys. */
const PLACEMENT_KEYS = ["namespace", "service_account_name",
                        "service_account_create"];
const ADVANCED_KEYS = ["platform", "openshift_cluster", "run_as_user"];
// Carried by every format, but the table is asked anyway.
const ENV_KEYS = ["extra_env"];

export function ConfigurePanel(p: ConfigurePanelProps) {
  // Answered once and shared with the rail, so the two cannot disagree.
  const placed = keysApply(PLACEMENT_KEYS, p.applies);
  // The placement fields left blank, from the one list warnings use.
  const coreBlanks = p.blanks.filter((k) => PLACEMENT_KEYS.includes(k));
  // Variables set, for the fold's summary and the rail (not a group).
  const envCount = envToRows(p.options.extra_env).length;
  // In connect mode a functionality the location does not run is not shown
  // (notRunPatch in App clears its options). Manual entry keeps every card,
  // since the card is the declaration; so does an unanswered location.
  const functionalities = p.sourceMode === "manual"
    ? p.functionalities
    : p.functionalities.filter((f) => runsFunctionality(p.enabled, f.id));
  // The first card on screen whose agent runs an engine states the size, once.
  // None on an SV-only location, where no engine size exists.
  const engineSizeOn = functionalities.find((f) => f.runs_engine)?.id;
  const secs = [
    ...functionalities.map((f) => ({
      id: "f-" + f.id, label: f.label,
      // An undeclared functionality owns nothing here, matching its card.
      gs: runsFunctionality(p.enabled, f.id)
        ? groupsFor(groupsOf(f.id), p.applies) : [],
      // Tells "running on defaults" from "not declared".
      off: p.enabled != null && !runsFunctionality(p.enabled, f.id),
      anchor: "cfg-f-" + f.id,
    })),
    ...(placed
      ? [{ id: "core", label: "Placement", gs: [] as OptionGroup[],
           off: false, anchor: "cfg-core" }]
      : []),
    { id: "shared", label: "Agent settings", off: false,
      gs: groupsFor(SHARED_GROUPS, p.applies), anchor: "cfg-shared" },
  ];
  /** A section's groups, as the rail worked them out. */
  const groupsIn = (id: string) => secs.find((s) => s.id === id)?.gs ?? [];
  return (
    <div className="space-y-4">
      {/* First and full width: it decides which of the other fields exist. */}
      <SegmentedControl
        label="Output format"
        value={p.format}
        onChange={p.setFormat}
        options={OUTPUT_FORMATS.map((f) => ({
          value: f.id, label: f.label, hint: f.hint,
        }))} />

      {/* Blank required fields, by option key as the bundle's README names them. */}
      {p.blanks.length > 0 && (
        <Callout tone="amber" className="text-2xs">
          {placeholderWarning(p.blanks)}
        </Callout>
      )}

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
          <p className="text-3xs font-semibold uppercase tracking-wide text-slate-400 px-2">
            In this bundle
          </p>
          {secs.map((s) => {
            // The switches, not detect(): a group just switched on shows here.
            const set = s.gs.filter((g) => p.grpOn[g.id]);
            // Plus the environment variables, counted from the option.
            const names = [
              ...set.map((g) => g.title),
              ...(s.id === "shared" && envCount
                ? [plural(envCount, "environment variable")]
                : []),
            ];
            // An unfinished group is a fault (red); a blank placement field is
            // allowed and becomes a marker (amber).
            const todo = s.id !== "core"
              && s.gs.some((g) => p.incomplete.includes(g));
            const gap = s.id === "core" && coreBlanks.length > 0;
            const detail = todo ? "needs attention"
              : gap ? "not filled in"
              : s.id === "core" ? String(p.options.namespace ?? "")
              : s.off ? "not enabled"
              : names.length ? names.join(", ")
              : "defaults";
            return (
              <a key={s.id} href={"#" + s.anchor}
                className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50">
                <span className={"mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full "
                  + (todo ? "bg-red-500"
                    : gap ? "bg-amber-400"
                    : names.length || s.id === "core" ? "bg-emerald-500"
                    : "bg-slate-300")} />
                <span className="min-w-0">
                  <span className="block text-xs font-medium text-slate-700">
                    {s.label}
                  </span>
                  <span className={"block text-3xs "
                    + (todo ? "text-red-600"
                      : gap ? "text-amber-600" : "text-slate-400")}>
                    {detail}
                  </span>
                </span>
              </a>
            );
          })}
        </nav>

        <div className="min-w-0 space-y-5">
          {/* The heading goes with its cards. */}
          {(functionalities.length > 0 || p.locUnclaimed.uncovered.length > 0
            || p.locUnclaimed.retired.length > 0) && (
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Deployment functionalities
            </h3>
            {/* Stacked: a card holds group rows. */}
            <div className="space-y-3">
              {/* The rail's own groups, so card and rail list the same. */}
              {functionalities.map((f) => (
                <FunctionalityCard key={f.id} {...p} feat={f}
                  statesEngineSize={f.id === engineSizeOn}
                  own={groupsIn("f-" + f.id)} />
              ))}
            </div>
            {/* Why one box clears the others, shown before the click. */}
            {p.sourceMode === "manual" && (
              <p className="text-2xs text-slate-500 mt-1.5">{SV_ALONE}</p>
            )}
            {/* A location that already runs both: warned, never blocked. */}
            {p.sourceMode === "connect"
              && svMixedWithEngines(p.enabled ?? [],
                                    engineFunctionalities(p.functionalities)) && (
              <Callout tone="amber" className="mt-1.5 text-2xs">
                {SV_MIXED}
              </Callout>
            )}
            {/* Ticking nothing is allowed and said. */}
            {p.sourceMode === "manual" && p.enabled?.length === 0 && (
              <Callout tone="amber" className="mt-1.5 text-2xs">
                Nothing is declared, so nothing says which funcIds this identity
                runs — and the images its bundle carries are chosen from those.
                Tick what it runs above.
              </Callout>
            )}
            {/* BlazeMeter's display names, not ids. */}
            {p.locUnclaimed.uncovered.length > 0 && (
              <p className="text-2xs text-slate-500 mt-1.5">
                Also runs{" "}
                <span className="text-slate-600">
                  {p.locUnclaimed.uncovered.join(", ")}</span> —
                no options here for those; nothing about them is generated or
                removed.
              </p>
            )}
            {/* Retired funcIds, as raw ids: the account no longer names them. */}
            {p.locUnclaimed.retired.length > 0 && (
              <p className="text-2xs text-slate-500 mt-1.5">
                Also carries{" "}
                <span className="font-mono text-slate-600">
                  {p.locUnclaimed.retired.join(", ")}</span> —
                the account no longer offers{" "}
                {p.locUnclaimed.retired.length > 1 ? "those funcIds" : "that funcId"},
                so this location predates the removal. Nothing here generates or
                removes {p.locUnclaimed.retired.length > 1 ? "them" : "it"}.
              </p>
            )}
          </section>
          )}

          {/* Where the agent goes in the cluster; docker bundles have no such place. */}
          {placed && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                Placement
              </h3>
              <div id="cfg-core"
                className="scroll-mt-4 rounded-xl border border-slate-200 p-3">
                <CoreFields {...p} />
              </div>
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Agent settings
            </h3>
            <div id="cfg-shared"
              className="scroll-mt-4 rounded-xl border border-slate-200 divide-y divide-slate-100">
              {rows(p, groupsIn("shared"))}
              {/* Every documented agent variable the groups above do not write. */}
              {keysApply(ENV_KEYS, p.applies) && (
                <FoldRow title="Environment variables"
                  hint="agent variables with no setting of their own above"
                  summary={envCount ? `${envCount} set` : undefined}>
                  {p.envArea}
                </FoldRow>
              )}
              {/* Advanced hides by the keys it writes. */}
              {keysApply(ADVANCED_KEYS, p.applies) && <AdvancedRow {...p} />}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
