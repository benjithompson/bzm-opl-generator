// Step 1: which location, which agent, and the credential that agent runs on.
// The selection and every write live in App; this panel renders the two lists,
// the forms, and the folds, and keeps only view state (filter, open rows, the
// regenerate confirmation, the new agent's name).
import { useEffect, useMemo, useRef, useState } from "react";
import { Api, Facts, FuncIdChoice, Location, Ship, SlotMinimum } from "../api";
import {
  Button, Callout, Check, Chevron, Collapse, ErrorMsg, Field, NoticeMsg,
  NumberInput, SecretInput, SegmentedControl, Spinner, SubSection, TextInput,
} from "../components";
import { LocationSettings } from "../groups/LocationSettings";
// Creating a location decides its funcIds, so the manual declaration's rule
// applies (sv.exclusiveWith).
import { toggleDeclared } from "../optionGroups";
import { exclusiveWith, SV_ALONE } from "../sv";
import { slotRule } from "../slots";
import { ManualSource } from "../groups/ManualSource";
import { onlineCount, shipOnline } from "../heartbeat";
import { useOpenRow } from "../openRow";
import { goneNotice, vanishedNotice } from "../stale";
import { rotateHazard } from "../token";
import { PlanAsk } from "../usePlan";
import { plural } from "../text";

/** The two ids typed by hand, for an account nobody here can reach. */
interface ManualIds { harbor_id: string; ship_id: string }

/** Where the three values come from: read off the account, or typed. */
interface SourceHandover {
  mode: "connect" | "manual";
  switchTo: (m: "connect" | "manual") => void;
  manual: ManualIds;
  setManual: (f: (m: ManualIds) => ManualIds) => void;
  /** Who the page is connected as; the key itself is the Account menu's. */
  who: { email: string; keyId: string } | null;
  /** Manual entry's facts are being read (the server asks the public
   *  registry, which takes seconds). */
  manualReading: boolean;
  /** What those facts cannot tell, in the server's plain prose. */
  manualWarnings: string[];
}

/** What a new location is asked for, in the account's own field names. The
 *  workspace comes from the nav drawer. */
interface LocationDraft {
  name: string;
  func_ids: string[];
  slots: number;
  threads_per_engine: number;
}

/** The new-location form: its fields, and App's write behind Create. */
interface NewLocationHandover {
  open: boolean;
  /** Also drops the last refusal. */
  setOpen: (v: boolean) => void;
  /** The workspace it would be created in, by name. */
  workspace: string | null;
  draft: LocationDraft;
  setDraft: (f: (d: LocationDraft) => LocationDraft) => void;
  /** What a location may be for: the served funcId vocabulary. */
  choices: FuncIdChoice[];
  /** Which of those funcIds run an engine (served), for exclusiveWith. */
  engines: string[];
  /** Slot minimums per funcId (served). Empty until read, refusing nothing. */
  minimums: Record<string, SlotMinimum>;
  /** What Create is waiting for, as the sentence it shows; "" when ready. */
  blockedBy: string;
  submit: () => Promise<void>;
}

/** The locations to choose from, the one chosen, and making a new one. */
interface LocationHandover {
  // Both chosen in the nav drawer's account menu; named here, not asked again.
  accountName: string | null;
  workspaceName: string | null;
  /** Every location in the workspace; the filter box here narrows it. */
  list: Location[];
  selectedId: string | null;
  pick: (id: string) => void;
  busy: boolean;
  error: string | null;
  /** Re-read the locations past the server's cache. Writes the list only. */
  refresh: () => void;
  /** Whether a refresh is in flight. Unlike `busy`, the list stays on screen. */
  refreshing: boolean;
  /** Put a location that came back changed into App's list. */
  updated: (loc: Location) => void;
  /** Has somebody confirmed this location? Selecting is not confirming, and
   *  choosing another withdraws it. */
  confirmed: boolean;
  confirm: () => void;
  create: NewLocationHandover;
}

/** The agents of the selected location, and making one. */
interface AgentHandover {
  id: string | null;
  pick: (id: string) => void;
  /** Reading this location's agents and images. */
  busy: boolean;
  facts: Facts | null;
  /** Whether the create form was asked for. A location with no agents shows it
   *  regardless. */
  showCreate: boolean;
  setShowCreate: (v: boolean) => void;
  /** Create an agent with this name; resolves true once it exists. */
  create: (name: string) => Promise<boolean>;
  error: string | null;
  /** The agent was created and only its credential was refused: a notice, not
   *  an error, so it does not invite a second create. */
  tokenNotice: string | null;
  /** Confirmed, as the location's; a lone agent is auto-picked, so picking is
   *  not enough to finish the step. */
  confirmed: boolean;
  confirm: () => void;
}

/** The credential the chosen agent runs on. */
interface CredentialHandover {
  token: string;
  setToken: (v: string) => void;
  /** Issue a new token for an existing agent. Resolves true once it is in the
   *  field, false if the agent changed meanwhile; rejects with the refusal. */
  regenerate: () => Promise<boolean>;
  /** Why the field is empty (token.recallNote), or null. */
  note: string | null;
}

interface AgentPanelProps {
  /** Passed through to the open location's settings, the one write on this step. */
  api: Api;
  source: SourceHandover;
  locations: LocationHandover;
  agents: AgentHandover;
  credential: CredentialHandover;
  /** What the sizing card states, passed to the open location's settings. */
  profile: PlanAsk;
}

/** Where the regenerate confirmation has got to, reset by changing agent. */
type Arm = "idle" | "armed" | "done";

/** The locations a filter leaves, trimmed and case-insensitive. */
function matching(list: Location[], query: string): Location[] {
  const q = query.trim().toLowerCase();
  return q ? list.filter((l) => l.name.toLowerCase().includes(q)) : list;
}

/** Above this many locations, the list gets a filter box. */
const FILTER_ABOVE = 8;

export function AgentPanel({
  api, source, locations, agents, credential, profile,
}: AgentPanelProps) {
  const location = locations.list.find((l) => l.id === locations.selectedId)
    ?? null;
  const ships: Ship[] = location?.ships ?? [];
  const [filter, setFilter] = useState("");
  const shown = useMemo(() => matching(locations.list, filter),
                        [locations.list, filter]);
  const [newName, setNewName] = useState("");
  const empty = !!location && ships.length === 0;
  /** The chosen agent's name, for the rows that name it. */
  const shipName = ships.find((x) => x.id === agents.id)?.name ?? null;
  // Picking an identity and minting one are one-of. A location with no agents
  // opens on the create form.
  const creating = agents.showCreate || ships.length === 0;
  const ship = ships.find((s) => s.id === agents.id);
  // An existing identity with no token in hand: its token cannot be read back.
  const reusing = !!ship && !credential.token;

  const [arm, setArm] = useState<Arm>("idle");
  const [issuing, setIssuing] = useState(false);
  const [issueErr, setIssueErr] = useState<string | null>(null);
  const [makingShip, setMakingShip] = useState(false);
  // Which row of each list is open; separate from what is selected.
  const agentRow = useOpenRow();
  const locRow = useOpenRow();
  // Which section is expanded: null follows the step; a click pins one, and
  // "none" is everything closed by hand.
  type Fold = "location" | "agent";
  const [pinned, setPinned] = useState<Fold | "none" | null>(null);
  // The selected location is not in the list after a Refresh. Not while the
  // first load is in flight, when the list is empty for another reason.
  const vanished = !!locations.selectedId && !location && !locations.busy;
  const reached: Fold = !locations.selectedId ? "location" : "agent";
  // `vanished` outranks a pin: only the location section can resolve it.
  const section = vanished ? "location" : (pinned ?? reached);
  const fold = (id: Fold) => ({
    open: section === id,
    onToggle: () => setPinned(section === id ? "none" : id),
  });
  // Disarm and open the agent's row whenever the agent changes, including an
  // auto-pick; a row closed by hand stays closed.
  const openAgentRow = agentRow.setOpen;
  useEffect(() => {
    setArm("idle"); setIssueErr(null); openAgentRow(agents.id);
  }, [agents.id, openAgentRow]);
  // Likewise one list up, including a restored selection.
  const openLocRow = locRow.setOpen;
  useEffect(() => {
    openLocRow(locations.selectedId);
  }, [locations.selectedId, openLocRow]);

  const toggle = (id: string) => {
    // Picking an agent by hand moves on from the location: follow the step again.
    if (agents.id !== id) { agents.pick(id); setPinned(null); return; }
    agentRow.toggle(id);
  };
  /** A location row: choosing one opens it; the chosen one folds and unfolds. */
  const toggleLocation = (l: Location) => {
    if (locations.selectedId !== l.id) {
      locations.pick(l.id);
      setPinned((l.ships ?? []).length ? "location" : null);
      return;
    }
    locRow.toggle(l.id);
  };
  /** Done with the location: fold its row and section, and open the agents. */
  const confirmLocation = () => {
    locations.confirm();
    locRow.setOpen(null);
    setPinned("agent");
  };
  /** Done with the agent: fold everything; the step is finished. */
  const confirmAgent = () => {
    agents.confirm();
    agentRow.setOpen(null);
    setPinned("none");
  };

  // The agent as of now, so an answer for the previous one is not reported here.
  const agentId = useRef(agents.id);
  agentId.current = agents.id;
  const regenerate = async () => {
    if (arm === "done" || issuing) return;
    if (arm === "idle") { setArm("armed"); return; }
    const forAgent = agents.id;
    setIssuing(true); setIssueErr(null);
    try {
      if (await credential.regenerate() && agentId.current === forAgent) setArm("done");
    } catch (e) {
      if (agentId.current !== forAgent) return;
      // The account's refusal, or a gone agent. Back to idle: nothing was issued.
      setIssueErr(goneNotice(e, "agent") ?? String((e as Error).message));
      setArm("idle");
    } finally { setIssuing(false); }
  };
  const createShip = async () => {
    setMakingShip(true);
    try {
      if (await agents.create(newName)) setNewName("");
    } finally { setMakingShip(false); }
  };

  return (
    <div className="space-y-3">
      <SegmentedControl
        value={source.mode}
        onChange={(v) => source.switchTo(v as "connect" | "manual")}
        options={[
          { value: "connect", label: "Connect to BlazeMeter",
            hint: "Pick a location and agent; a new agent's token is issued once, when you create it.",
            // Nothing to pick from without a key.
            disabledReason: source.who ? undefined
              : "connect an account first — the key at the foot of the menu" },
          { value: "manual", label: "Enter values manually",
            hint: "For an account you cannot reach — generation only, nothing is checked." },
        ]} />

      {source.mode === "manual" ? (
        <ManualSource
          harborId={source.manual.harbor_id}
          shipId={source.manual.ship_id}
          authToken={credential.token}
          reading={source.manualReading} warnings={source.manualWarnings}
          onHarborId={(v) => source.setManual((m) => ({ ...m, harbor_id: v }))}
          onShipId={(v) => source.setManual((m) => ({ ...m, ship_id: v }))}
          onAuthToken={credential.setToken} />
      ) : (
        <>
          <SubSection title="Private location" done={!!locations.selectedId}
            {...fold("location")}
            action={
              /* Needs a key; connect mode only, since this branch is. */
              <Button kind="ghost" onClick={locations.refresh}
                busy={locations.refreshing} disabled={!source.who}>
                Refresh
              </Button>}
            summary={location
              ? `${location.name} · ${location.slots ?? "?"} engine(s)/agent`
              : "none selected"}
            hint="A location holds agents. Open one to see what the sizing
                  above would change about it, and to save that change.">
            <div className="space-y-3">
              {/* Which account and workspace this list is from. */}
              <p className="text-2xs text-slate-500">
                {locations.accountName ? (
                  <>Locations in <b>{locations.workspaceName ?? "every workspace"}</b>
                  {" · "}{locations.accountName}. Change either at the foot of the menu.</>
                ) : (
                  <span className="text-amber-700">
                    Choose an account at the foot of the menu to list its
                    locations.
                  </span>
                )}
              </p>
              {/* The selection survived a Refresh its location did not. */}
              <NoticeMsg msg={vanished ? vanishedNotice("location") : null} />
              {/* Outside the form, so a refusal stays visible with it open. */}
              <ErrorMsg msg={locations.error} />
              {locations.create.open ? (
                <NewLocation create={locations.create} />
              ) : (
                <Button kind="ghost" disabled={!source.who}
                  onClick={() => locations.create.setOpen(true)}>
                  + New location
                </Button>
              )}
              {locations.list.length > FILTER_ABOVE && (
                <TextInput value={filter} onChange={setFilter}
                  placeholder={`filter ${locations.list.length} locations…`} />
              )}
              {locations.busy && (
                <p className="flex items-center gap-2 text-xs text-slate-500">
                  <Spinner className="text-bzm" /> reading this workspace&apos;s locations…
                </p>
              )}
              {/* Zebra rows with a visible divider, so it reads as a list. */}
              <div className={"max-h-[32rem] overflow-y-auto border border-slate-300 rounded-md divide-y divide-slate-200 "
                + (locations.busy ? "opacity-40" : "")}>
                {shown.map((l, i) => {
                  const n = (l.ships ?? []).length;
                  const up = onlineCount(l.ships);
                  // Chosen and open are separate: a row folds and stays chosen.
                  const on = l.id === locations.selectedId;
                  const isOpen = locRow.open === l.id;
                  return (
                    <div key={l.id}
                      className={on ? "bg-bzm/10 border-l-4 border-bzm"
                        : i % 2 ? "bg-slate-50/70" : "bg-white"}>
                      {/* Selecting pins the location section open on its
                          settings; clicking an agent releases it. A second click
                          on the chosen row folds its body. */}
                      <button type="button" onClick={() => toggleLocation(l)}
                        aria-expanded={isOpen}
                        className="w-full text-left px-3 py-2.5 text-sm hover:bg-slate-100/60 flex items-center gap-2">
                        <span className={"h-1.5 w-1.5 rounded-full shrink-0 "
                          + (n ? "bg-emerald-500" : "bg-amber-400")} />
                        <span className="font-medium">{l.name}</span>
                        <span className="text-xs text-slate-400 truncate">
                          {l.slots} engine{l.slots === 1 ? "" : "s"}/agent
                          {l.threadsPerEngine
                            ? ` × ${l.threadsPerEngine.toLocaleString()} VUs` : ""}
                        </span>
                        <span className="grow" />
                        <span className={"text-2xs " + (n ? "text-slate-500" : "text-amber-700")}>
                          {n ? `${plural(n, "agent")}${up ? ` · ${up} online` : ""}`
                             : "no agents yet"}
                        </span>
                        {/* Follows the body, not the selection. */}
                        <Chevron open={isOpen} />
                      </button>
                      <Collapse open={isOpen}>
                        {locRow.shown(l.id) && (
                          <div className="px-3 pb-3">
                            <LocationSettings api={api} location={l}
                              profile={profile}
                              onUpdated={locations.updated}
                              onConfirm={confirmLocation} />
                          </div>
                        )}
                      </Collapse>
                    </div>
                  );
                })}
                {!!source.who && shown.length === 0 && !locations.busy && (
                  <p className="px-3 py-2 text-sm text-slate-400">no locations match</p>
                )}
              </div>
            </div>
          </SubSection>

          <SubSection title="Agent (ship)" done={!!agents.id} {...fold("agent")}
            summary={agents.id
              ? (ships.find((x) => x.id === agents.id)?.name ?? agents.id)
              : (location ? "none selected" : "pick a location first")}
            hint="One agent = one deployment, inside the location above.">
            <div className="space-y-3">
              {agents.busy && (
                <p className="flex items-center gap-2 text-xs text-slate-500">
                  <Spinner className="text-bzm" /> reading this location&apos;s agents…
                </p>
              )}
              {!agents.busy && !location && (
                <p className="text-xs text-slate-400">Pick a location above first.</p>
              )}
              {!agents.busy && empty && (
                <Callout tone="amber">
                  <p className="text-xs text-amber-900">
                    <b>{location!.name}</b> has no agents yet — nothing is
                    deployed to it.
                  </p>
                  <p className="text-2xs text-amber-700 mt-0.5">
                    Create the first one below; its AUTH_TOKEN is issued then, once.
                  </p>
                </Callout>
              )}
              {!agents.busy && location && (
                <>
                  {creating ? (
                    <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
                      <p className="text-xs font-semibold text-slate-700">
                        {empty ? "Create the first agent in this location"
                               : "New agent in this location"}
                      </p>
                      <Field label="Name">
                        <TextInput value={newName} onChange={setNewName}
                          placeholder="e.g. k8s-prod-cluster" />
                      </Field>
                      <div className="flex gap-2 items-center">
                        {/* Busy while it waits: a second click is a second agent. */}
                        <Button disabled={!locations.selectedId || !newName}
                          busy={makingShip} onClick={createShip}>
                          {makingShip ? "Creating…" : "Create"}
                        </Button>
                        {ships.length > 0 && !makingShip && (
                          <Button kind="ghost" onClick={() => agents.setShowCreate(false)}>
                            Cancel
                          </Button>
                        )}
                      </div>
                    </div>
                  ) : (
                    <Button kind="ghost" onClick={() => agents.setShowCreate(true)}>
                      + New agent identity (recommended)
                    </Button>
                  )}

                  {ships.length > 0 && (
                    <div className="border border-slate-300 rounded-md divide-y divide-slate-200">
                      {ships.map((s, i) => {
                        const up = shipOnline(s);
                        const on = s.id === agents.id;
                        const isOpen = agentRow.open === s.id;
                        return (
                          // Selected in the same blue as a location.
                          <div key={s.id} className={on ? "bg-bzm/10"
                            : i % 2 ? "bg-slate-50/70" : "bg-white"}>
                            {/* A div: a button would nest the buttons inside it. */}
                            <div onClick={() => toggle(s.id)}
                              className={"w-full text-left px-3 py-2.5 text-sm hover:bg-slate-100 flex items-center gap-2 cursor-pointer "
                                + (on ? "border-l-4 border-bzm" : "")}>
                              <Chevron open={isOpen} className="text-xs w-3 text-center" />
                              <span className={"h-1.5 w-1.5 rounded-full shrink-0 "
                                + (up ? "bg-emerald-500" : "bg-slate-300")} />
                              <span className="font-medium">{s.name || s.id}</span>
                              <span className="text-xs text-slate-400 truncate">
                                {up ? "online" : s.state}
                              </span>
                              <span className="grow" />
                              {on && (credential.token || arm === "done") && (
                                <span className="text-2xs text-emerald-700">
                                  {arm === "done" ? "token regenerated" : "token in hand"}
                                </span>
                              )}
                              {!on && <span className="text-2xs text-slate-400">reuse</span>}
                            </div>

                            <Collapse open={isOpen}>
                              {agentRow.shown(s.id) && (
                                <div className="px-3 pb-3 pl-10 space-y-2">
                                  <label className="block">
                                    <span className="text-xs font-medium text-slate-600">
                                      Agent AUTH_TOKEN
                                    </span>
                                    <SecretInput value={credential.token}
                                      onChange={credential.setToken}
                                      placeholder="paste the token this agent was created with" />
                                  </label>
                                  <div className="flex items-center gap-2 flex-wrap">
                                    {/* Nothing changes until this is pressed twice. */}
                                    {(reusing || arm === "done") && (
                                      <button type="button"
                                        disabled={issuing || arm === "done"}
                                        onClick={(e) => { e.stopPropagation(); regenerate(); }}
                                        className={"text-2xs font-semibold rounded px-2 py-1 flex items-center gap-1.5 " + ({
                                          idle: "bg-red-600 text-white hover:bg-red-700",
                                          armed: "bg-red-800 text-white hover:bg-red-900 ring-2 ring-red-300",
                                          done: "bg-slate-200 text-slate-500 cursor-default",
                                        })[arm]}>
                                        {issuing && <Spinner className="text-white" />}
                                        {issuing ? "Regenerating…"
                                          : { idle: "Regenerate token",
                                              armed: "I'm sure",
                                              done: "Regenerated" }[arm]}
                                      </button>
                                    )}
                                    {/* A way out of the armed state. */}
                                    {arm === "armed" && !issuing && (
                                      <button type="button"
                                        onClick={(e) => { e.stopPropagation(); setArm("idle"); }}
                                        className="text-2xs font-medium rounded px-2 py-1 border border-slate-300 text-slate-600 hover:bg-slate-100">
                                        Cancel
                                      </button>
                                    )}
                                    {/* "could not ask" and "holds none" read differently. */}
                                    {reusing && arm === "idle" && !issuing
                                      && credential.note && (
                                      <span className="text-2xs text-slate-500">
                                        {credential.note}
                                      </span>
                                    )}
                                  </div>

                                  {arm === "armed" && (
                                    <Callout tone="red">
                                      <p className="text-xs font-semibold text-red-900">
                                        This kills the token {s.name || s.id} is running on.
                                      </p>
                                      <p className="text-2xs text-red-800 mt-0.5">
                                        {rotateHazard(s.id)} A new agent instead
                                        costs nothing and leaves that install alone.
                                      </p>
                                    </Callout>
                                  )}
                                  {arm === "done" && (
                                    <Callout tone="emerald" className="text-xs">
                                      New AUTH_TOKEN for <b>{s.name || s.id}</b>, in
                                      the field above — this bundle is the only
                                      copy. Re-apply it wherever that agent was
                                      running.
                                    </Callout>
                                  )}
                                  <ErrorMsg msg={issueErr} />
                                  {up && (
                                    <Callout tone="amber" className="text-xs">
                                      <b>{s.name || s.id}</b> is online — already
                                      running somewhere. A second deployment on
                                      it will conflict.
                                    </Callout>
                                  )}
                                </div>
                              )}
                            </Collapse>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              )}
              <ErrorMsg msg={agents.error} />
              <NoticeMsg msg={agents.tokenNotice} />
              {agents.facts && (
                <p className="text-xs text-slate-500">
                  image inventory: {agents.facts.images_source} · functionalities:{" "}
                  {agents.facts.func_ids?.join(", ")}
                </p>
              )}
              {/* Confirming the agent, like the location: a lone agent is
                  auto-picked, so somebody still has to say it is the one. */}
              {!agents.busy && location && !empty && (
                <div className="flex items-center gap-2 border-t border-slate-100 pt-3">
                  <span className="text-2xs text-slate-500">
                    {!agents.id
                      ? "pick the agent this bundle is for"
                      : agents.confirmed
                        ? `confirmed — ${shipName ?? agents.id}`
                        : `${shipName ?? agents.id} — Confirm to finish this step`}
                  </span>
                  <span className="grow" />
                  <Button disabled={!agents.id} onClick={confirmAgent}>
                    Confirm
                  </Button>
                </div>
              )}
            </div>
          </SubSection>
        </>
      )}
    </div>
  );
}

/** The new-location form. The write is App's `submit`; busy while it waits,
 *  since a second click would create a second location. */
function NewLocation({ create }: { create: NewLocationHandover }) {
  const [busy, setBusy] = useState(false);
  const { draft, setDraft } = create;
  const rule = slotRule(draft.func_ids, create.minimums);
  const submit = async () => {
    setBusy(true);
    try { await create.submit(); } finally { setBusy(false); }
  };
  return (
    <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
      <p className="text-xs font-semibold text-slate-700">
        New private location
      </p>
      {/* The workspace is named, not asked: it is the drawer's. */}
      <Field required
        label={`Name (created in workspace: ${create.workspace ?? "?"})`}>
        <TextInput value={draft.name}
          onChange={(v) => setDraft((d) => ({ ...d, name: v }))} /></Field>
      <div className="flex gap-4 items-end">
        <div className="flex gap-3 flex-wrap">
          {create.choices.map((c) => (
            <Check key={c.id} label={c.label}
              checked={draft.func_ids.includes(c.id)}
              // Service virtualization is created alone; toggleDeclared keeps
              // the list in box order.
              onChange={(on) => setDraft((d) => ({
                ...d,
                func_ids: toggleDeclared(d.func_ids, c.id, on,
                                         create.choices.map((x) => x.id),
                                         exclusiveWith(create.engines)),
              }))} />
          ))}
          {/* Said before a tick is taken away, not after. */}
          <p className="basis-full text-2xs text-slate-500">{SV_ALONE}</p>
        </div>
        {/* The minimum is on the field from the moment the box is ticked; the
            refusal below is BlazeMeter's own words. Nothing raises it for you. */}
        <Field label="Slots"
          hint={rule ? `${rule.label} needs at least ${rule.minimum}`
                     : "concurrent engines"}>
          <NumberInput className="w-20" value={String(draft.slots)}
            onChange={(v) => setDraft((d) => ({ ...d, slots: Number(v) }))} />
        </Field>
        <Field label="Threads per engine"
          hint="required — tests can't start without it">
          <NumberInput className="w-24" value={String(draft.threads_per_engine)}
            onChange={(v) =>
              setDraft((d) => ({ ...d, threads_per_engine: Number(v) }))} />
        </Field>
      </div>
      {/* Create greys out and says what it is waiting for. */}
      <div className="flex gap-2 items-center">
        <Button disabled={!!create.blockedBy} busy={busy} onClick={submit}>
          {busy ? "Creating…" : "Create"}
        </Button>
        <Button kind="ghost" onClick={() => create.setOpen(false)}>
          Cancel
        </Button>
        {create.blockedBy && (
          <span className="text-2xs text-amber-700">{create.blockedBy}</span>
        )}
      </div>
    </div>
  );
}
