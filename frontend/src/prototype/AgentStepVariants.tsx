// THROWAWAY -- three ways to make the location -> agent relationship visible,
// and an empty location impossible to miss.
//
// Today a location with no agents drops you straight into the create form with
// no sentence anywhere saying the location is empty, and the list row says
// "0 agent(s)" in the same grey as everything else. So:
//
//   K  annotate in place -- an agent-count badge on every row, and the agent
//      panel opens with what the chosen location actually has
//   L  master-detail -- locations left, that location's agents right, with a
//      real empty state where the list would be
//   M  a path line (account > workspace > location > agent) that always says
//      where you are and what is still missing
//
// Only the location list and the agent picker are rebuilt here. The account /
// workspace selects and the create-location block are App's own, passed in as
// nodes, because they are not what is being judged and a second copy would rot.

import { ReactNode, useEffect, useRef, useState } from "react";
import { Facts, Location, Ship } from "../api";
import {
  Button, ErrorMsg, NoticeMsg, SecretInput, Spinner, SubSection, TextInput,
} from "../components";
// The rotation's consequence, in the words the download step already uses --
// said here as well because this is where the identity is chosen, and the
// download is where it is too late to choose differently.
import { rotateHazard } from "../token";

export type AgentKey = "K" | "L" | "M";

export interface AgentStepProps {
  locations: Location[];
  filteredLocs: Location[];
  locFilter: string;
  setLocFilter: (v: string) => void;
  harborId: string | null;
  setHarborId: (id: string) => void;
  location: Location | null;
  ships: Ship[];
  shipId: string | null;
  pickShip: (id: string) => void;
  shipOnline: (s: Ship) => boolean;
  locLabels: (l: Location) => string[];
  /** The create-agent form's state and action, App's own -- creating a ship is
   *  a real write to the account, so nothing here re-implements it. */
  creating: boolean;
  setCreating: (v: boolean) => void;
  newShipName: string;
  setNewShipName: (v: string) => void;
  createShip: () => void | Promise<void>;
  shipErr: string | null;
  shipTokenNotice: string | null;
  facts: Facts | null;
  who: unknown;
  accountWorkspace: ReactNode;
  createLocationBlock: ReactNode;
  /** Whether the next download issues a new AUTH_TOKEN, and the setter. M turns
   *  it on by choosing an identity that already exists: reusing one means the
   *  bundle needs a credential for it, and the only way to get one is to issue
   *  a new one -- which is the same thing as saying the running install stops
   *  working until this bundle is applied. */
  rotate: boolean;
  setRotate: (v: boolean) => void;
  /** Is a token already in hand (pasted, or issued when this agent was
   *  created)? Then nothing has to be regenerated, and core would ignore a
   *  rotation anyway -- the token in the form wins. */
  hasToken: boolean;
  /** Writes the AUTH_TOKEN field. M fills it in from the Regenerate button, so
   *  the credential is in hand before the download rather than issued by it. */
  setAuthToken: (v: string) => void;
  /** The credential itself. M keeps the field inside the expanded agent row --
   *  it belongs to that identity, and nowhere else on the page says so. */
  authToken: string;
  /** Requests in flight. Two, not one: a location list that has arrived while
   *  its facts are still coming is a state worth showing as itself. */
  locBusy: boolean;
  factsBusy: boolean;
}

// -- the Regenerate action ---------------------------------------------------
// STUB. Issuing a token for an existing ship is a real, destructive write to
// the account -- it kills the credential the running agent is using -- and
// there is no HTTP route for it here: rotation happens inside generate/download
// (`rotate_token`), never on its own. Shipping this button therefore needs one
// new route over core.rotate_auth_token; until then it mints something that
// could not possibly be mistaken for a credential, so the flow can be judged
// without touching anybody's agent.
const stubToken = (shipId: string) => `PROTOTYPE_STUB_NOT_A_REAL_TOKEN_${shipId}`;

/** The wait a real rotation would cost, so the spinner is on screen long enough
 *  to be judged. A stub that returned instantly would make the loading state
 *  untestable, which is half of what was asked for. */
const stubIssue = (shipId: string) => new Promise<string>((resolve) =>
  setTimeout(() => resolve(stubToken(shipId)), 900));

/** Where the confirm button has got to. Per ship, and reset by changing ship:
 *  "Regenerated" is a statement about one identity, and carrying it to the next
 *  row would claim a rotation that never happened. */
type Arm = "idle" | "armed" | "done";

/** What a location's agents amount to, in one place: three variants say it and
 *  they must not word it differently. `none` is the case this prototype exists
 *  for -- it is a state, not a count of zero. */
function agentSummary(l: Location, online: (s: Ship) => boolean) {
  const ships = l.ships ?? [];
  if (!ships.length) return { none: true, text: "no agents yet", cls: "bg-amber-100 text-amber-800" };
  const up = ships.filter(online).length;
  return {
    none: false,
    text: `${ships.length} agent${ships.length === 1 ? "" : "s"}`
      + (up ? ` · ${up} online` : ""),
    cls: up ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600",
  };
}

/** The one sentence an empty location needs, wherever it is shown. Deploying is
 *  the reason it matters: an empty location is not broken, it just has nothing
 *  running, and the next click is always the same one. */
function EmptyLocation({ name }: { name: string }) {
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5">
      <p className="text-xs text-amber-900">
        <b>{name}</b> has no agents yet — nothing is deployed to it and no
        bundle can name one.
      </p>
      <p className="text-[11px] text-amber-700 mt-0.5">
        Create the first agent below. Its AUTH_TOKEN is issued once, at that
        moment, and kept in the field under this step.
      </p>
    </div>
  );
}

/** The create-agent form, identical in all three -- it is App's action with a
 *  name field in front of it, and not the thing being prototyped. */
function CreateAgent(p: AgentStepProps & { first: boolean; busy?: boolean }) {
  return (
    <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
      <p className="text-xs font-semibold text-slate-700">
        {p.first ? "Create the first agent in this location" : "New agent in this location"}
      </p>
      <label className="block">
        <span className="text-xs font-medium text-slate-600">Name</span>
        <TextInput value={p.newShipName} onChange={p.setNewShipName}
          placeholder="e.g. k8s-prod-cluster" />
      </label>
      <div className="flex gap-2 items-center">
        {/* Creating a ship is a round trip that also issues its token; the
            button says so while it waits rather than looking ignored, which is
            how a second click -- and a second agent -- happens. */}
        <Button disabled={!p.harborId || !p.newShipName || p.busy}
          onClick={p.createShip}>
          {p.busy ? "Creating…" : "Create"}
        </Button>
        {p.busy && <Spinner className="text-bzm" />}
        {p.ships.length > 0 && !p.busy && (
          <Button kind="ghost" onClick={() => p.setCreating(false)}>Cancel</Button>
        )}
      </div>
    </div>
  );
}

function AgentChips(p: AgentStepProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {p.ships.map((s) => (
        <button key={s.id} onClick={() => p.pickShip(s.id)}
          className={"px-3 py-1.5 rounded-md border text-sm "
            + (s.id === p.shipId
              ? "border-bzm bg-bzm/10 text-bzm-dark font-medium"
              : "border-slate-300 hover:bg-slate-50")}>
          {s.name || s.id}{" "}
          <span className={"text-xs " + (p.shipOnline(s) ? "text-emerald-600" : "text-slate-400")}>
            ({p.shipOnline(s) ? "online" : s.state})
          </span>
        </button>
      ))}
    </div>
  );
}

/** The hazard of reusing an identity that is already running. Same words as the
 *  shipped page; it is not what changes between variants. */
function OnlineWarning(p: AgentStepProps) {
  const sel = p.ships.find((s) => s.id === p.shipId);
  if (!sel || !p.shipOnline(sel)) return null;
  return (
    <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
      <b>{sel.name}</b> is currently online — it's already running somewhere.
      Deploying a second agent with the same identity will conflict.
    </p>
  );
}

function Footer(p: AgentStepProps) {
  return (
    <>
      <ErrorMsg msg={p.shipErr} />
      <NoticeMsg msg={p.shipTokenNotice} />
      {p.facts && (
        <p className="text-xs text-slate-500">
          image inventory: {p.facts.images_source} · features: {p.facts.func_ids?.join(", ")}
        </p>
      )}
    </>
  );
}

// == K -- annotate in place ===================================================
// The two panels stay as they are; what changes is that every row states its
// agents, an empty one says so in amber rather than in the same grey as its
// slot count, and the agent panel opens with a line about the location it
// belongs to instead of a bare form.
function VariantK(p: AgentStepProps) {
  const empty = !!p.location && p.ships.length === 0;
  return (
    <>
      <SubSection title="Private location" done={!!p.harborId}
        hint="The location = harbor. Each row says how many agents it already has.">
        <div className="space-y-3">
          {p.accountWorkspace}
          {p.locations.length > 8 && (
            <TextInput value={p.locFilter} onChange={p.setLocFilter}
              placeholder={`filter ${p.locations.length} locations…`} />
          )}
          <div className="max-h-56 overflow-y-auto border border-slate-200 rounded-md divide-y divide-slate-100">
            {p.filteredLocs.map((l) => {
              const a = agentSummary(l, p.shipOnline);
              return (
                <button key={l.id} onClick={() => p.setHarborId(l.id)}
                  className={"w-full text-left px-3 py-2 text-sm hover:bg-slate-50 "
                    + (l.id === p.harborId ? "bg-bzm/10 border-l-4 border-bzm" : "")}>
                  <span className="flex items-center gap-2">
                    <span className="font-medium">{l.name}</span>
                    {p.locLabels(l).map((label) => (
                      <span key={label}
                        className="text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 bg-slate-100 text-slate-600">
                        {label}
                      </span>
                    ))}
                    <span className="grow" />
                    {/* The badge this variant is for: the agent count is the
                        thing you came to the list to find out, so it is not
                        the fourth clause of a grey sentence. */}
                    <span className={"text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 " + a.cls}>
                      {a.text}
                    </span>
                  </span>
                  <span className="text-xs text-slate-400">
                    {l.funcIds?.slice(0, 4).join(", ")} · {l.slots} slot{l.slots === 1 ? "" : "s"}
                  </span>
                </button>
              );
            })}
            {!!p.who && p.filteredLocs.length === 0 && (
              <p className="px-3 py-2 text-sm text-slate-400">no locations match</p>
            )}
          </div>
          {p.createLocationBlock}
        </div>
      </SubSection>

      <SubSection title="Agent (ship)" done={!!p.shipId}
        hint={p.location
          ? `Agents belonging to ${p.location.name}.`
          : "Pick a location first — agents belong to one."}>
        <div className="space-y-3">
          {!p.location && (
            <p className="text-xs text-slate-400">Nothing to show until a location is chosen.</p>
          )}
          {empty && <EmptyLocation name={p.location!.name} />}
          {p.location && !empty && !p.creating && (
            <>
              <p className="text-xs font-medium text-slate-600">
                {p.ships.length} agent{p.ships.length === 1 ? "" : "s"} in{" "}
                <b>{p.location.name}</b> — reuse one only if you are replacing
                that install:
              </p>
              <AgentChips {...p} />
              <OnlineWarning {...p} />
              <Button kind="ghost" onClick={() => p.setCreating(true)}>
                + New agent identity (recommended)
              </Button>
            </>
          )}
          {p.location && (empty || p.creating) && <CreateAgent {...p} first={empty} />}
          <Footer {...p} />
        </div>
      </SubSection>
    </>
  );
}

// == L -- master / detail =====================================================
// The containment is the layout: locations on the left, the selected one's
// agents on the right, so "an agent belongs to a location" is not a sentence
// you have to read. Empty is then a real empty state -- the panel where the
// agents would be says there are none.
function VariantL(p: AgentStepProps) {
  const empty = !!p.location && p.ships.length === 0;
  return (
    <SubSection title="Location & agent" done={!!p.shipId}
      hint="A location holds agents. Pick the location on the left, its agent on the right.">
      <div className="space-y-3">
        {p.accountWorkspace}
        <div className="grid grid-cols-2 gap-3 items-start">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 mb-1">
              Locations
            </p>
            {p.locations.length > 8 && (
              <TextInput value={p.locFilter} onChange={p.setLocFilter}
                placeholder={`filter ${p.locations.length}…`} />
            )}
            <div className="mt-1 max-h-72 overflow-y-auto border border-slate-200 rounded-md divide-y divide-slate-100">
              {p.filteredLocs.map((l) => {
                const a = agentSummary(l, p.shipOnline);
                return (
                  <button key={l.id} onClick={() => p.setHarborId(l.id)}
                    className={"w-full text-left px-3 py-2 hover:bg-slate-50 "
                      + (l.id === p.harborId ? "bg-bzm/10 border-l-4 border-bzm" : "")}>
                    <span className="block text-sm font-medium truncate">{l.name}</span>
                    <span className={"text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 " + a.cls}>
                      {a.text}
                    </span>
                    <span className="text-[11px] text-slate-400 ml-1.5">
                      {l.slots} slot{l.slots === 1 ? "" : "s"}
                    </span>
                  </button>
                );
              })}
            </div>
            {p.createLocationBlock}
          </div>

          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500 mb-1">
              Agents {p.location ? <>in {p.location.name}</> : ""}
            </p>
            <div className="border border-slate-200 rounded-md p-3 min-h-[8rem] space-y-3">
              {!p.location ? (
                <p className="text-xs text-slate-400">
                  Select a location to see its agents.
                </p>
              ) : empty ? (
                // The empty state proper: where the list would be, saying what
                // is not there. This is the case the shipped page skips over.
                <div className="text-center py-3 space-y-2 border border-dashed border-amber-300 bg-amber-50/60 rounded-md">
                  <p className="text-sm font-medium text-amber-900">
                    This location has no agents
                  </p>
                  <p className="text-[11px] text-amber-700 px-4">
                    Nothing is deployed to <b>{p.location.name}</b>. Create the
                    first one — its AUTH_TOKEN is issued once, at that moment.
                  </p>
                </div>
              ) : (
                <>
                  <AgentChips {...p} />
                  <OnlineWarning {...p} />
                </>
              )}
              {p.location && (empty || p.creating
                ? <CreateAgent {...p} first={empty} />
                : <Button kind="ghost" onClick={() => p.setCreating(true)}>
                    + New agent identity (recommended)
                  </Button>)}
            </div>
          </div>
        </div>
        <Footer {...p} />
      </div>
    </SubSection>
  );
}

// == M -- a path that says where you are ======================================
// One line above the panels, always present, naming every level down to the
// agent -- so the missing one is missing *in the path*, not merely absent from
// a panel further down. The panels themselves stay stacked and plain.
function VariantM(p: AgentStepProps) {
  const empty = !!p.location && p.ships.length === 0;
  const ship = p.ships.find((s) => s.id === p.shipId);
  // An identity that already existed, with no credential in hand for it: its
  // token was issued once, when it was created, and no API reads one back.
  // Selecting the row does NOT rotate -- that is what the button below is for.
  const reusing = !!ship && !p.hasToken;
  const [arm, setArm] = useState<Arm>("idle");
  const [issuing, setIssuing] = useState(false);
  // Which row is open. Separate from `shipId` on purpose: closing a row is a
  // view action and must not un-choose the agent the bundle is for.
  const [open, setOpen] = useState<string | null>(null);
  const [makingShip, setMakingShip] = useState(false);
  // Disarm on every change of ship, and open that ship's row. Keyed on shipId
  // rather than on the click so the lone-agent auto-pick opens its row too --
  // and so collapsing a row (which leaves shipId alone) stays collapsed instead
  // of springing back open.
  useEffect(() => { setArm("idle"); setOpen(p.shipId); }, [p.shipId]);
  // One open row at a time, which is what a single `open` id is: opening the
  // second closes the first without either of them being asked to.
  const toggle = (id: string) => {
    if (p.shipId !== id) { p.pickShip(id); return; }
    setOpen((cur) => (cur === id ? null : id));
  };
  // The row on its way out. Its body has to stay mounted for the length of the
  // transition, or switching agents collapses the old row in one frame while
  // the new one animates -- which is the jump the animation exists to remove.
  const [closing, setClosing] = useState<string | null>(null);
  const wasOpen = useRef<string | null>(null);
  useEffect(() => {
    const prev = wasOpen.current;
    wasOpen.current = open;
    if (!prev || prev === open) return;
    setClosing(prev);
    const t = setTimeout(() => setClosing(null), 200);
    return () => clearTimeout(t);
  }, [open]);
  const regenerate = async () => {
    if (arm === "done" || issuing) return;
    if (arm === "idle") { setArm("armed"); return; }
    setIssuing(true);
    const token = await stubIssue(String(p.shipId));
    p.setAuthToken(token);
    // The token is now in hand, so the download must not issue a second one --
    // core's rule is that a token in the form wins, and leaving the rotate box
    // ticked would have the page promise a rotation that will not happen.
    p.setRotate(false);
    setIssuing(false);
    setArm("done");
  };
  const createShip = async () => {
    setMakingShip(true);
    try { await p.createShip(); } finally { setMakingShip(false); }
  };
  const seg = (label: string, value: string | null, warn = false) => (
    <span className="flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className={"text-xs font-medium "
        + (value ? "text-slate-800" : warn ? "text-amber-700" : "text-slate-400")}>
        {value ?? (warn ? "none yet" : "—")}
      </span>
    </span>
  );
  return (
    <>
      <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 flex items-center gap-2 flex-wrap">
        {seg("location", p.location?.name ?? null)}
        <span className="text-slate-300">›</span>
        {/* The agent segment is the point: with a location chosen and no agent
            in it, the path says so before you reach the panel that would. */}
        {seg("agent", ship?.name ?? null, !!p.location)}
        {empty && (
          <span className="text-[11px] text-amber-700 ml-1">
            — this location is empty; the first agent has to be created
          </span>
        )}
      </div>

      <SubSection title="Private location" done={!!p.harborId}
        hint="The location = harbor. Agents belong to it.">
        <div className="space-y-3">
          {p.accountWorkspace}
          {/* Above the list, like the agent panel below: the two pickers now
              read the same way down the page -- make one, or choose one. */}
          {p.createLocationBlock}
          {p.locations.length > 8 && (
            <TextInput value={p.locFilter} onChange={p.setLocFilter}
              placeholder={`filter ${p.locations.length} locations…`} />
          )}
          {p.locBusy && (
            <p className="flex items-center gap-2 text-xs text-slate-500">
              <Spinner className="text-bzm" /> reading this workspace&apos;s locations…
            </p>
          )}
          {/* A list has to look like one. Zebra banding plus a divider a shade
              darker than the card's own borders is enough -- rows that share a
              background and a hairline read as one block of text. */}
          <div className={"max-h-56 overflow-y-auto border border-slate-300 rounded-md divide-y divide-slate-200 "
            + (p.locBusy ? "opacity-40" : "")}>
            {p.filteredLocs.map((l, i) => {
              const a = agentSummary(l, p.shipOnline);
              return (
                <button key={l.id} onClick={() => p.setHarborId(l.id)}
                  className={"w-full text-left px-3 py-2.5 text-sm hover:bg-slate-100 flex items-center gap-2 "
                    + (l.id === p.harborId ? "bg-bzm/10 border-l-4 border-bzm"
                      : i % 2 ? "bg-slate-50/70" : "bg-white")}>
                  <span className={"h-1.5 w-1.5 rounded-full shrink-0 "
                    + (a.none ? "bg-amber-400" : "bg-emerald-500")} />
                  <span className="font-medium">{l.name}</span>
                  <span className="text-xs text-slate-400 truncate">
                    {l.slots} slot{l.slots === 1 ? "" : "s"}
                  </span>
                  <span className="grow" />
                  <span className={"text-[11px] " + (a.none ? "text-amber-700" : "text-slate-500")}>
                    {a.text}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </SubSection>

      <SubSection title="Agent (ship)" done={!!p.shipId}
        hint="One agent = one deployment. It lives inside the location above.">
        <div className="space-y-3">
          {/* Reading the location: its agents and its image inventory arrive
              together, and an empty list before they land would read as an
              empty location -- the one thing this variant exists to say
              clearly. */}
          {p.factsBusy && (
            <p className="flex items-center gap-2 text-xs text-slate-500">
              <Spinner className="text-bzm" /> reading this location&apos;s agents…
            </p>
          )}
          {!p.factsBusy && empty && <EmptyLocation name={p.location!.name} />}
          {!p.factsBusy && !empty && p.location && (
            <>
              {/* Above the list, not under it: creating a new identity is the
                  recommended path, and a button below a scrolling list is the
                  last thing found and reads as an afterthought. */}
              {!p.creating && (
                <Button kind="ghost" onClick={() => p.setCreating(true)}>
                  + New agent identity (recommended)
                </Button>
              )}
              {p.creating && <CreateAgent {...p} first={false} createShip={createShip} busy={makingShip} />}

              {/* One row open at a time. Everything about an identity -- its
                  credential, and the button that replaces it -- is inside the
                  row it belongs to, so nothing on the page refers to "the
                  selected agent" from somewhere else. */}
              <div className="border border-slate-300 rounded-md divide-y divide-slate-200">
                {p.ships.map((s, i) => {
                  const up = p.shipOnline(s);
                  const on = s.id === p.shipId;
                  const isOpen = open === s.id;
                  return (
                    // Banded like the location list above, for the same reason
                    // and in the same shades.
                    <div key={s.id} className={on ? "bg-bzm/5"
                      : i % 2 ? "bg-slate-50/70" : "bg-white"}>
                      {/* A div, not a button: the row is clickable and so are
                          the controls inside it, and a button inside a button
                          is not valid HTML -- the browser unnests it and the
                          inner one stops receiving its own clicks. */}
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
                        {on && (p.hasToken || arm === "done") && (
                          <span className="text-[11px] text-emerald-700">
                            {arm === "done" ? "token regenerated" : "token in hand"}
                          </span>
                        )}
                        {!on && <span className="text-[11px] text-slate-400">reuse</span>}
                      </div>

                      {/* Animated open/close. The grid-rows 0fr -> 1fr trick
                          because the panel's height is not knowable in advance
                          and `height: auto` does not transition. 180ms: enough
                          to see which row moved, not enough to wait for. The
                          body stays mounted while the row is closing, or a
                          switch between two agents would collapse the old one
                          instantly and animate only the new one. */}
                      <div className={"grid transition-[grid-template-rows] duration-[180ms] ease-out "
                        + (isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
                        <div className="overflow-hidden">
                        {(isOpen || closing === s.id) && (
                        <div className="px-3 pb-3 pl-10 space-y-2">
                          <label className="block">
                            <span className="text-xs font-medium text-slate-600">
                              Agent AUTH_TOKEN
                            </span>
                            <SecretInput value={p.authToken}
                              onChange={p.setAuthToken}
                              placeholder="paste the token this agent was created with" />
                          </label>
                          <div className="flex items-center gap-2 flex-wrap">
                            {/* The regeneration is an act, not a consequence of
                                having clicked the row: nothing about this
                                identity changes until this is pressed twice. */}
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
                                : { idle: "Regenerate token", armed: "Are you sure?",
                                    done: "Regenerated" }[arm]}
                            </button>
                            {!p.hasToken && arm === "idle" && (
                              <span className="text-[11px] text-slate-500">
                                its token was issued once, when this agent was
                                created, and no API reads one back
                              </span>
                            )}
                          </div>

                          {/* What the press is about to cost, and what it did.
                              The hazard is the download step\'s own sentence, so
                              the two cannot drift. */}
                          {arm === "armed" && (
                            <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2">
                              <p className="text-xs font-semibold text-red-900">
                                This takes down whatever is running as{" "}
                                {s.name || s.id}.
                              </p>
                              <p className="text-[11px] text-red-800 mt-0.5">
                                {rotateHazard(s.id)}
                              </p>
                              <p className="text-[11px] text-red-800 mt-0.5">
                                Creating a new agent instead costs nothing and
                                leaves that install alone. Press again to confirm.
                              </p>
                            </div>
                          )}
                          {arm === "done" && (
                            <p className="text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-md px-3 py-2">
                              A new AUTH_TOKEN was issued for <b>{s.name || s.id}</b>{" "}
                              and put in the field above — this bundle is the only
                              copy. The previous one is dead: re-apply this bundle
                              wherever that agent was running.{" "}
                              <span className="font-semibold">PROTOTYPE: the value
                              is a stub, not a credential — issuing a real one needs
                              an endpoint this build does not have.</span>
                            </p>
                          )}
                          {up && (
                            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                              <b>{s.name || s.id}</b> is online — it is already
                              running somewhere. Deploying a second agent on this
                              identity will conflict.
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
            </>
          )}
          {!p.factsBusy && p.location && empty && (
            <CreateAgent {...p} first createShip={createShip} busy={makingShip} />
          )}
          {!p.location && (
            <p className="text-xs text-slate-400">Pick a location above first.</p>
          )}
          <Footer {...p} />
        </div>
      </SubSection>
    </>
  );
}

/** `variant` null renders nothing and App keeps its own two panels. */
export function AgentStep(p: AgentStepProps & { variant: AgentKey }) {
  const V = { K: VariantK, L: VariantL, M: VariantM }[p.variant];
  return <V {...p} />;
}
