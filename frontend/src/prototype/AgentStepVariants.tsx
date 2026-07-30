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

import { ReactNode } from "react";
import { Facts, Location, Ship } from "../api";
import { Button, ErrorMsg, NoticeMsg, SubSection, TextInput } from "../components";

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
  createShip: () => void;
  shipErr: string | null;
  shipTokenNotice: string | null;
  facts: Facts | null;
  who: unknown;
  accountWorkspace: ReactNode;
  createLocationBlock: ReactNode;
}

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
function CreateAgent(p: AgentStepProps & { first: boolean }) {
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
      <div className="flex gap-2">
        <Button disabled={!p.harborId || !p.newShipName} onClick={p.createShip}>
          Create
        </Button>
        {p.ships.length > 0 && (
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
          {p.locations.length > 8 && (
            <TextInput value={p.locFilter} onChange={p.setLocFilter}
              placeholder={`filter ${p.locations.length} locations…`} />
          )}
          <div className="max-h-56 overflow-y-auto border border-slate-200 rounded-md divide-y divide-slate-100">
            {p.filteredLocs.map((l) => {
              const a = agentSummary(l, p.shipOnline);
              return (
                <button key={l.id} onClick={() => p.setHarborId(l.id)}
                  className={"w-full text-left px-3 py-2 text-sm hover:bg-slate-50 flex items-center gap-2 "
                    + (l.id === p.harborId ? "bg-bzm/10 border-l-4 border-bzm" : "")}>
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
          {p.createLocationBlock}
        </div>
      </SubSection>

      <SubSection title="Agent (ship)" done={!!p.shipId}
        hint="One agent = one deployment. It lives inside the location above.">
        <div className="space-y-3">
          {empty && <EmptyLocation name={p.location!.name} />}
          {!empty && p.location && !p.creating && (
            <>
              <AgentChips {...p} />
              <OnlineWarning {...p} />
              <Button kind="ghost" onClick={() => p.setCreating(true)}>
                + New agent identity (recommended)
              </Button>
            </>
          )}
          {p.location && (empty || p.creating) && <CreateAgent {...p} first={empty} />}
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
