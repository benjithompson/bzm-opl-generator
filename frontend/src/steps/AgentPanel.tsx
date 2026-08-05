// Step 1: which location, which agent, and the credential that agent runs on.
//
// A location holds agents; an agent is one deployment. That containment is what
// the step is built around now -- a path line that names both, two lists that
// look alike because they are the same kind of choice, and the agent's
// credential inside the agent's own row rather than in a field further down the
// page that could belong to anything.
//
// The state stays in App: `harborId` and `shipId` are what everything
// downstream is generated from, and the effects that clear the token when
// either moves live there too. What changed (#103) is the interface -- four
// records in DownloadPanel's shape rather than thirty-six props, and three
// things this file used to be handed that it can answer for itself:
//
//   * the filtered list. It arrived beside the full one, from a filter this
//     panel renders the box for. Two lists side by side are two answers to one
//     question: the count on the placeholder came off `locations` and the rows
//     under it off `filteredLocs`, free to be about different lists.
//   * the freshness rule. A predicate passed down is a rule with no tests of
//     its own -- see heartbeat.ts, which is now the only statement of it.
//   * the create-location form, which arrived as a finished element. The write
//     behind it is still App's (see NewLocation below); what moved here is the
//     markup, which is the half that belongs beside the agent form it is a pair
//     with.
import { useEffect, useMemo, useState } from "react";
import { Api, Facts, FuncIdChoice, Location, Ship } from "../api";
import {
  Button, Check, ErrorMsg, Field, NoticeMsg, NumberInput,
  SecretInput, SegmentedControl, Spinner, SubSection, TextInput,
} from "../components";
import { LocationSettings } from "../groups/LocationSettings";
import { ManualSource } from "../groups/ManualSource";
// Whether an agent is reporting, from one module rather than from a function
// this panel is handed. Two readers here -- the count on a location's row and
// the state on an agent's -- and they were the same call twice.
import { onlineCount, shipOnline } from "../heartbeat";
import { useOpenRow } from "../openRow";
import { rotateHazard } from "../token";
// What the profile card above this panel is sizing, on its way to the one
// location panel that measures itself against it.
import { PlanAsk } from "../usePlan";

/** The two ids typed by hand, for an account nobody here can reach. */
export interface ManualIds { harbor_id: string; ship_id: string }

/** Where the three values come from: read off the account, or typed. */
export interface SourceHandover {
  mode: "connect" | "manual";
  switchTo: (m: "connect" | "manual") => void;
  manual: ManualIds;
  setManual: (f: (m: ManualIds) => ManualIds) => void;
  /** Who the page is connected as. Read here, never asked for: the key is the
   *  Account menu's, and what this step needs is whether there is one -- a
   *  location list needs a key, not the form that supplies it. */
  who: { email: string; keyId: string } | null;
}

/** What a new location is being asked for. The four fields the account takes,
 *  in its own names, so nothing is renamed between this form and the request.
 *  `workspace_id` is not here: the workspace is chosen at the foot of the nav
 *  drawer and the write picks it up there, which is why the name field says
 *  which one it is about to write into. */
export interface LocationDraft {
  name: string;
  func_ids: string[];
  slots: number;
  threads_per_engine: number;
}

/** Making a private location: the form's fields, and the one call that writes
 *  it to the account.
 *
 *  `submit` is App's. Creating a location is one of the writes CLAUDE.md holds
 *  to the rule that a request touching the account is made where its cost is on
 *  screen -- so this panel renders the fields and the button, and what the
 *  button does stays a named function in App, exactly as the agent form beside
 *  it already worked. Nothing here can reach the client, so no click can grow
 *  into a second write by accident. */
export interface NewLocationHandover {
  open: boolean;
  /** Also drops whatever the last attempt was refused for: an error about a
   *  form that is no longer on screen describes nothing. */
  setOpen: (v: boolean) => void;
  /** The workspace it would be created in, by name. */
  workspace: string | null;
  draft: LocationDraft;
  setDraft: (f: (d: LocationDraft) => LocationDraft) => void;
  /** What a location may be for. Served (facts.CATEGORY_BY_FUNC), never spelled
   *  in the frontend -- the copy that used to be here lost sv-bridge. */
  choices: FuncIdChoice[];
  /** What Create is waiting for, as the sentence it shows; "" when ready. */
  blockedBy: string;
  submit: () => Promise<void>;
}

/** The locations to choose from, the one chosen, and making a new one. */
export interface LocationHandover {
  // Both are chosen at the foot of the nav drawer (AccountMenu), because every
  // view reads the account and the location list is the only thing here the
  // workspace narrows -- so this step names them rather than asking again.
  accountName: string | null;
  workspaceName: string | null;
  /** Every location in that workspace. One list: the box below narrows it, and
   *  the panel that renders the box is the one that applies it. */
  list: Location[];
  filter: string;
  setFilter: (v: string) => void;
  selectedId: string | null;
  pick: (id: string) => void;
  busy: boolean;
  error: string | null;
  /** The location came back changed: App owns the list and the selection, so
   *  it is App that puts it back. */
  updated: (loc: Location) => void;
  /** Has this location been confirmed? Not "is one selected": the settings
   *  under it are a real read, and step 1 is finished when somebody has said so
   *  rather than when a row happens to be highlighted. Withdrawn by choosing a
   *  different location -- App holds which one was confirmed, not a flag. */
  confirmed: boolean;
  confirm: () => void;
  create: NewLocationHandover;
}

/** The agents inside the selected location, and making one.
 *
 *  No list of its own: the agents are the selected location's, and a second
 *  copy passed in beside it is a copy that can be about a different location
 *  than the row the user is looking at. */
export interface AgentHandover {
  id: string | null;
  pick: (id: string) => void;
  /** Reading this location's agents and images. */
  busy: boolean;
  facts: Facts | null;
  /** Whether the create form was asked for. Whether it is *shown* is not this:
   *  a location with no agents has nothing to pick, so it opens on the form
   *  regardless -- see `creating` below, which is a view's decision and made in
   *  the view. */
  showCreate: boolean;
  setShowCreate: (v: boolean) => void;
  newName: string;
  setNewName: (v: string) => void;
  create: () => Promise<void>;
  error: string | null;
  /** The agent WAS created and only its credential was refused. In the red
   *  error slot that reads as a failed creation, and the next click makes a
   *  second agent. */
  tokenNotice: string | null;
  /** As the location's: confirmed, and withdrawn by choosing another agent.
   *  A lone agent is auto-picked, so without this the whole step could complete
   *  itself and the one screen naming what the bundle is for would never have
   *  been looked at. */
  confirmed: boolean;
  confirm: () => void;
}

/** The credential the chosen agent runs on. */
export interface CredentialHandover {
  token: string;
  setToken: (v: string) => void;
  /** Issue a new one for an agent that already exists. Resolves once the token
   *  is in the field; throws with the account's own refusal if it is refused. */
  regenerate: () => Promise<void>;
  /** Why the field is empty, or null where there is nothing to say.
   *
   *  Written by token.recallNote rather than chosen here, because the choice is
   *  between the two states this codebase never lets share a representation: an
   *  agent this app minted nothing for, and one it could not be asked about.
   *  Null covers both "there is a token in the field" and "the answer has not
   *  arrived yet" -- neither is a sentence, and the second must not borrow the
   *  first's. */
  note: string | null;
}

export interface AgentPanelProps {
  /** Passed straight through to the open location's settings, which is where
   *  the one write on this step is made (DownloadPanel takes the client for the
   *  same reason). Nothing in this file calls a route itself. */
  api: Api;
  source: SourceHandover;
  locations: LocationHandover;
  agents: AgentHandover;
  credential: CredentialHandover;
  /** What the sizing above this panel states. Passed through to
   *  the open location's settings, which is where a profile turns into four
   *  numbers about one location -- and where the only control that writes them
   *  to the account lives. Sizing needs no account, so this is not a record
   *  about the connection and does not belong in any of the four above. */
  profile: PlanAsk;
}

/** Where the confirm has got to. Per agent, and reset by changing agent:
 *  "Regenerated" is a statement about one identity. */
type Arm = "idle" | "armed" | "done";

/** The rows a query leaves. Trimmed, because a filter pasted with a trailing
 *  space is a paste artefact rather than a search for one. */
function matching(list: Location[], query: string): Location[] {
  const q = query.trim().toLowerCase();
  return q ? list.filter((l) => l.name.toLowerCase().includes(q)) : list;
}

/** Above this many, the list gets a filter box. A real account has 171
 *  locations and eight fit on the screen they are on, so the box appears where
 *  scrolling stops being enough and not before. */
const FILTER_ABOVE = 8;

export function AgentPanel({
  api, source, locations, agents, credential, profile,
}: AgentPanelProps) {
  // Derived here rather than passed in beside the list: both are answers to
  // "which location", and only one of them can be the list's.
  const location = locations.list.find((l) => l.id === locations.selectedId)
    ?? null;
  const ships: Ship[] = location?.ships ?? [];
  const shown = useMemo(() => matching(locations.list, locations.filter),
                        [locations.list, locations.filter]);
  const empty = !!location && ships.length === 0;
  /** The chosen agent's name, for the rows that name it. */
  const shipName = ships.find((x) => x.id === agents.id)?.name ?? null;
  // Which half of the agent section is on screen -- picking an identity and
  // minting one are one-of, because reusing an identity that is already running
  // conflicts with that install while creating one is free. Derived, not a
  // second piece of state: a location with no agents has nothing to pick, so it
  // opens on the create form, and creating the first agent drops back to the
  // list showing it. The same derivation is why Cancel appears only when there
  // is a list to go back to.
  const creating = agents.showCreate || ships.length === 0;
  const ship = ships.find((s) => s.id === agents.id);
  // An identity that already existed, with no credential in hand for it: its
  // token was issued once, at creation, and no API reads one back. Selecting
  // the row does not rotate anything -- the button below is what does.
  const reusing = !!ship && !credential.token;

  const [arm, setArm] = useState<Arm>("idle");
  const [issuing, setIssuing] = useState(false);
  const [issueErr, setIssueErr] = useState<string | null>(null);
  const [makingShip, setMakingShip] = useState(false);
  // Which row of each list is open. Separate from what the list selects,
  // because closing a row is a view action and must not un-choose the location
  // or the agent the bundle is for -- see openRow.ts.
  const agentRow = useOpenRow();
  const locRow = useOpenRow();
  // Which of the three sections is expanded. `null` means "wherever the step
  // has got to", which is what makes the panel open on the next thing to do
  // rather than on all of it; a click pins one and stops it moving underneath
  // whoever clicked -- a section that re-folds itself when the state behind it
  // changes is the worst of both.
  type Fold = "location" | "agent";
  // null = follow the step; "none" = the user closed the open one and wants all
  // three folded. Three states rather than two, because "closed everything" is
  // a choice and re-opening the current step over it would fight the click.
  const [pinned, setPinned] = useState<Fold | "none" | null>(null);
  const reached: Fold = !locations.selectedId ? "location" : "agent";
  const section = pinned ?? reached;
  const fold = (id: Fold) => ({
    open: section === id,
    onToggle: () => setPinned(section === id ? "none" : id),
  });
  // Disarm on every change of agent, and open that agent's row. Keyed on shipId
  // rather than on the click so the lone-agent auto-pick opens its row too, and
  // so a row closed by hand stays closed.
  useEffect(() => {
    setArm("idle"); setIssueErr(null); agentRow.setOpen(agents.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agents.id]);
  // The same, one list up: choosing a location opens it, including when the
  // choice was a session restore rather than a click.
  useEffect(() => {
    locRow.setOpen(locations.selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locations.selectedId]);

  const toggle = (id: string) => {
    // Choosing an agent by hand is the move on from the location, so the fold
    // goes back to following the step -- which lands it here. Picking the
    // location pinned it open (below); this is what releases it.
    if (agents.id !== id) { agents.pick(id); setPinned(null); return; }
    agentRow.toggle(id);
  };
  /** The location list's rows. A row that is not selected is chosen, which
   *  opens it; the one that is already chosen folds and unfolds, because the
   *  body is long and hiding it is not a reason to generate for somewhere
   *  else. */
  const toggleLocation = (l: Location) => {
    if (locations.selectedId !== l.id) {
      locations.pick(l.id);
      setPinned((l.ships ?? []).length ? "location" : null);
      return;
    }
    locRow.toggle(l.id);
  };
  /** Done with the location: fold its row and its whole section away, and open
   *  the agent list under it.
   *
   *  The one move the panel could not make on its own. `pinned` follows the
   *  step until something is clicked, and choosing a location pins it *open* --
   *  correctly, since its settings are the next thing to read -- so nothing
   *  released it again except picking an agent, which is inside the section
   *  that was in the way. Both halves are needed: the section carries the
   *  fold, and the row carries the settings form, which would otherwise still
   *  be open behind it the next time the section is expanded. */
  const confirmLocation = () => {
    locations.confirm();
    locRow.setOpen(null);
    setPinned("agent");
  };
  /** ...and done with the agent: fold it away too. Nothing opens after it --
   *  the step is finished, which is what Next now waits for. */
  const confirmAgent = () => {
    agents.confirm();
    agentRow.setOpen(null);
    setPinned("none");
  };

  const regenerate = async () => {
    if (arm === "done" || issuing) return;
    if (arm === "idle") { setArm("armed"); return; }
    setIssuing(true); setIssueErr(null);
    try {
      await credential.regenerate();
      setArm("done");
    } catch (e) {
      // The account's own refusal, which names the ship and says a token read
      // off the BlazeMeter UI works just as well. Back to idle: nothing was
      // issued, so nothing was lost, and the button has to be pressable again.
      setIssueErr(String((e as Error).message));
      setArm("idle");
    } finally { setIssuing(false); }
  };
  const createShip = async () => {
    setMakingShip(true);
    try { await agents.create(); } finally { setMakingShip(false); }
  };

  return (
    <div className="space-y-3">
      <SegmentedControl
        value={source.mode}
        onChange={(v) => source.switchTo(v as "connect" | "manual")}
        options={[
          { value: "connect", label: "Connect to BlazeMeter",
            hint: "Pick a location and agent; a new agent's token is issued once, when you create it.",
            // Nothing to pick from without a key, and the key is not this
            // step's to ask for any more -- it is the key at the foot of the nav drawer.
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
          onHarborId={(v) => source.setManual((m) => ({ ...m, harbor_id: v }))}
          onShipId={(v) => source.setManual((m) => ({ ...m, ship_id: v }))}
          onAuthToken={credential.setToken} />
      ) : (
        <>
          {/* One block, in one place, whatever state it is in. It used to
              swap for a single "Connected as ..." line, so connecting made the
              whole step jump and disconnecting made it jump back -- and the
              way out moved with it. The fields stay put and describe the key
              in use; the button that connected is the button that
              disconnects. */}
          <SubSection title="Private location" done={!!locations.selectedId}
            {...fold("location")}
            summary={location
              ? `${location.name} · ${location.slots ?? "?"} engine(s)/agent`
              : "none selected"}
            hint="A location holds agents. Open one to see what the capacity
                  profile would change about it, and to save that change.">
            <div className="space-y-3">
              {/* Neither picker is here any more: both are at the foot of the
                  nav drawer with the key, because the account decides what
                  three separate views show and the workspace comes with it.
                  What is left is the sentence saying which of them this list
                  is -- a list of locations with no idea which account they are
                  from is the thing the pickers were really for. */}
              <p className="text-[11px] text-slate-500">
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
              {/* Outside the form rather than only beside the button that opens
                  it: a refused create leaves the form open, so an error shown
                  only in the closed state is an error nobody sees. */}
              <ErrorMsg msg={locations.error} />
              {/* Above the list, like the agent panel below: the two read the
                  same way down the page -- make one, or choose one. */}
              {locations.create.open ? (
                <NewLocation create={locations.create} />
              ) : (
                <Button kind="ghost" disabled={!source.who}
                  onClick={() => locations.create.setOpen(true)}>
                  + New location
                </Button>
              )}
              {locations.list.length > FILTER_ABOVE && (
                <TextInput value={locations.filter} onChange={locations.setFilter}
                  placeholder={`filter ${locations.list.length} locations…`} />
              )}
              {locations.busy && (
                <p className="flex items-center gap-2 text-xs text-slate-500">
                  <Spinner className="text-bzm" /> reading this workspace&apos;s locations…
                </p>
              )}
              {/* A list has to look like one: zebra banding and a divider a
                  shade darker than the card's own border. Rows that share a
                  background and a hairline read as one block of text. */}
              <div className={"max-h-[32rem] overflow-y-auto border border-slate-300 rounded-md divide-y divide-slate-200 "
                + (locations.busy ? "opacity-40" : "")}>
                {shown.map((l, i) => {
                  const n = (l.ships ?? []).length;
                  const up = onlineCount(l.ships);
                  // Chosen and open are two things now: the row folds without
                  // giving up being the location the bundle is for.
                  const on = l.id === locations.selectedId;
                  const isOpen = locRow.open === l.id;
                  return (
                    // A div wrapping the row and its body, like an agent row:
                    // the settings belong to this location, so they open out of
                    // it rather than sitting under the list where they would
                    // read as the list's.
                    <div key={l.id}
                      className={on ? "bg-bzm/10 border-l-4 border-bzm"
                        : i % 2 ? "bg-slate-50/70" : "bg-white"}>
                      {/* Pinned open by the click that selects: the row opens
                          onto what the sizing would change about this
                          location and the one control that saves it, and a
                          section that folds itself the moment you act on it
                          takes that decision off screen. It was worse than it
                          sounds -- a location with one idle agent is auto-picked,
                          so the panel could arrive and be hidden in the same
                          frame. Clicking an agent below releases it.

                          Not for an empty location: it has no agent to run
                          anything under, so the next thing is creating one and
                          the fold should go where it always went.

                          A second click on the same header folds the body back
                          up. Before, the only way to put that much text away was
                          to click a different location -- which changes what is
                          being generated in order to hide something. */}
                      <button onClick={() => toggleLocation(l)}
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
                        <span className={"text-[11px] " + (n ? "text-slate-500" : "text-amber-700")}>
                          {n ? `${n} agent${n === 1 ? "" : "s"}${up ? ` · ${up} online` : ""}`
                             : "no agents yet"}
                        </span>
                        {/* The chevron follows the body, not the selection:
                            it is the control's own state, and a chosen row
                            folded shut points down at nothing. */}
                        <span className={"text-slate-400 text-xs transition-transform duration-150 "
                          + (isOpen ? "rotate-90" : "")}>›</span>
                      </button>
                      <div className={"grid transition-[grid-template-rows] duration-[180ms] ease-out "
                        + (isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
                        <div className="overflow-hidden">
                          {locRow.shown(l.id) && (
                            <div className="px-3 pb-3">
                              <LocationSettings api={api} location={l}
                                profile={profile}
                                onUpdated={locations.updated}
                                onConfirm={confirmLocation} />
                            </div>
                          )}
                        </div>
                      </div>
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
                <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5">
                  <p className="text-xs text-amber-900">
                    <b>{location!.name}</b> has no agents yet — nothing is
                    deployed to it.
                  </p>
                  <p className="text-[11px] text-amber-700 mt-0.5">
                    Create the first one below; its AUTH_TOKEN is issued then, once.
                  </p>
                </div>
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
                        <TextInput value={agents.newName} onChange={agents.setNewName}
                          placeholder="e.g. k8s-prod-cluster" />
                      </Field>
                      <div className="flex gap-2 items-center">
                        {/* Creating an agent is a round trip that also issues
                            its token; the button says so while it waits rather
                            than looking ignored, which is how a second click --
                            and a second agent -- happens. */}
                        <Button disabled={!locations.selectedId || !agents.newName}
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
                          // Selected is the same blue as a selected location
                          // above: the two lists are the same kind of choice,
                          // and a fainter tint here read as banding rather than
                          // as selection.
                          <div key={s.id} className={on ? "bg-bzm/10"
                            : i % 2 ? "bg-slate-50/70" : "bg-white"}>
                            {/* A div, not a button: the row is clickable and so
                                are the controls inside it, and a button inside a
                                button is not valid HTML -- the browser unnests
                                it and the inner one stops receiving clicks. */}
                            <div onClick={() => toggle(s.id)}
                              className={"w-full text-left px-3 py-2.5 text-sm hover:bg-slate-100 flex items-center gap-2 cursor-pointer "
                                + (on ? "border-l-4 border-bzm" : "")}>
                              <span className="text-slate-400 text-xs w-3 shrink-0">
                                {isOpen ? "▾" : "▸"}
                              </span>
                              <span className={"h-1.5 w-1.5 rounded-full shrink-0 "
                                + (up ? "bg-emerald-500" : "bg-slate-300")} />
                              <span className="font-medium">{s.name || s.id}</span>
                              <span className="text-xs text-slate-400 truncate">
                                {up ? "online" : s.state}
                              </span>
                              <span className="grow" />
                              {on && (credential.token || arm === "done") && (
                                <span className="text-[11px] text-emerald-700">
                                  {arm === "done" ? "token regenerated" : "token in hand"}
                                </span>
                              )}
                              {!on && <span className="text-[11px] text-slate-400">reuse</span>}
                            </div>

                            {/* Animated open/close. grid-rows 0fr -> 1fr because
                                the panel's height is not knowable in advance and
                                `height: auto` does not transition. */}
                            <div className={"grid transition-[grid-template-rows] duration-[180ms] ease-out "
                              + (isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
                              <div className="overflow-hidden">
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
                                      {/* Regenerating is an act, not a
                                          consequence of having clicked the row:
                                          nothing changes until this is pressed
                                          twice. */}
                                      {(reusing || arm === "done") && (
                                        <button
                                          disabled={issuing || arm === "done"}
                                          onClick={(e) => { e.stopPropagation(); regenerate(); }}
                                          className={"text-[11px] font-semibold rounded px-2 py-1 flex items-center gap-1.5 " + ({
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
                                      {/* Armed has to have a way out, or the
                                          only exits are the destructive button
                                          and closing the row. */}
                                      {arm === "armed" && !issuing && (
                                        <button
                                          onClick={(e) => { e.stopPropagation(); setArm("idle"); }}
                                          className="text-[11px] font-medium rounded px-2 py-1 border border-slate-300 text-slate-600 hover:bg-slate-100">
                                          Cancel
                                        </button>
                                      )}
                                      {/* Whichever of the two it is. It used to
                                          be one sentence, because there was one
                                          state: nothing remembered a token, so
                                          an empty field could only mean the
                                          agent predated this app's knowledge of
                                          it. Now the field can be empty because
                                          the store could not be asked, and
                                          saying "cannot be read back" over that
                                          would be a claim about the agent made
                                          without asking. */}
                                      {reusing && arm === "idle" && !issuing
                                        && credential.note && (
                                        <span className="text-[11px] text-slate-500">
                                          {credential.note}
                                        </span>
                                      )}
                                    </div>

                                    {arm === "armed" && (
                                      <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2">
                                        <p className="text-xs font-semibold text-red-900">
                                          This kills the token {s.name || s.id} is running on.
                                        </p>
                                        <p className="text-[11px] text-red-800 mt-0.5">
                                          {rotateHazard(s.id)} A new agent instead
                                          costs nothing and leaves that install alone.
                                        </p>
                                      </div>
                                    )}
                                    {arm === "done" && (
                                      <p className="text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-md px-3 py-2">
                                        New AUTH_TOKEN for <b>{s.name || s.id}</b>, in
                                        the field above — this bundle is the only
                                        copy. Re-apply it wherever that agent was
                                        running.
                                      </p>
                                    )}
                                    <ErrorMsg msg={issueErr} />
                                    {up && (
                                      <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                                        <b>{s.name || s.id}</b> is online — already
                                        running somewhere. A second deployment on
                                        it will conflict.
                                      </p>
                                    )}
                                  </div>
                                )}
                              </div>
                            </div>
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
              {/* The same row, in the same place, as the location's above: what
                  is being confirmed on the left, the control on the right.
                  Nothing to write here -- an agent is chosen, not edited -- so
                  the only reason this button exists is the one the location's
                  Confirm turned out to need as well: somebody has to say the
                  choice is made. A lone agent is auto-picked, so without it the
                  step could complete itself. */}
              {!agents.busy && location && !empty && (
                <div className="flex items-center gap-2 border-t border-slate-100 pt-3">
                  <span className="text-[11px] text-slate-500">
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

/** The new-location form: four fields, and App's write behind Create.
 *
 *  Rendered here rather than handed over as a finished element (#103), and the
 *  distinction that makes it safe is that `submit` is still App's -- this file
 *  has no client and cannot reach the account, so what a click costs is decided
 *  in one place and said in this one. The agent form above it has worked that
 *  way all along; a location arriving pre-rendered was the odd one out.
 *
 *  Busy while it waits, like that form and for the same reason: this is a round
 *  trip to BlazeMeter, and a button that looks ignored gets a second click --
 *  which here means a second location in the customer's account. */
function NewLocation({ create }: { create: NewLocationHandover }) {
  const [busy, setBusy] = useState(false);
  const { draft, setDraft } = create;
  const submit = async () => {
    setBusy(true);
    try { await create.submit(); } finally { setBusy(false); }
  };
  return (
    <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
      <p className="text-xs font-semibold text-slate-700">
        New private location
      </p>
      {/* The workspace is named on the field rather than asked for: it is the
          one at the foot of the drawer, and this write lands in it. */}
      <Field required
        label={`Name (created in workspace: ${create.workspace ?? "?"})`}>
        <TextInput value={draft.name}
          onChange={(v) => setDraft((d) => ({ ...d, name: v }))} /></Field>
      <div className="flex gap-4 items-end">
        <div className="flex gap-3 flex-wrap">
          {create.choices.map((c) => (
            <Check key={c.id} label={c.label}
              checked={draft.func_ids.includes(c.id)}
              onChange={(on) => setDraft((d) => ({
                ...d,
                func_ids: on ? [...d.func_ids, c.id]
                  : d.func_ids.filter((x) => x !== c.id),
              }))} />
          ))}
        </div>
        <Field label="Slots" hint="concurrent engines">
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
      {/* Create stays put and greys out, and says which of the two things it is
          waiting for -- a button that disables itself without a reason is the
          same dead end as one that disappears. */}
      <div className="flex gap-2 items-center">
        <Button disabled={!!create.blockedBy} busy={busy} onClick={submit}>
          {busy ? "Creating…" : "Create"}
        </Button>
        <Button kind="ghost" onClick={() => create.setOpen(false)}>
          Cancel
        </Button>
        {create.blockedBy && (
          <span className="text-[11px] text-amber-700">{create.blockedBy}</span>
        )}
      </div>
    </div>
  );
}
