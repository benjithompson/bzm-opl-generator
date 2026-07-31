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
// either moves live there too.
import { useEffect, useRef, useState } from "react";
import { Facts, Location, Ship } from "../api";
import {
  Button, Check, ErrorMsg, Field, inputCls, NoticeMsg, SearchSelect,
  SecretInput, SegmentedControl, Spinner, SubSection, TextInput,
} from "../components";
import { LocationSettings } from "../groups/LocationSettings";
import { ManualSource } from "../groups/ManualSource";
import { rotateHazard } from "../token";

export interface AgentPanelProps {
  // -- where the three values come from
  sourceMode: "connect" | "manual";
  switchMode: (m: "connect" | "manual") => void;
  manual: { harbor_id: string; ship_id: string };
  setManual: (f: (m: { harbor_id: string; ship_id: string }) =>
    { harbor_id: string; ship_id: string }) => void;
  sourceOpen: boolean;
  setSourceOpen: (v: boolean) => void;
  // -- the API key
  who: { email: string; keyId: string } | null;
  /** Hand the key back. The server forgets it; a key saved to disk stays. */
  disconnect: () => void;
  keyPath: string;
  setKeyPath: (v: string) => void;
  pasteId: string;
  setPasteId: (v: string) => void;
  pasteSecret: string;
  setPasteSecret: (v: string) => void;
  saveKey: boolean;
  setSaveKey: (v: boolean) => void;
  connect: (body: { path?: string; id?: string; secret?: string; save?: boolean }) => void;
  connErr: string | null;
  setConnErr: (v: string | null) => void;
  connecting: boolean;
  // -- the account tree
  accounts: { id: number; name: string }[];
  accountId: number | null;
  setAccountId: (id: number) => void;
  workspaces: { id: number; name: string }[];
  workspaceId: number | null;
  setWorkspaceId: (id: number) => void;
  locations: Location[];
  filteredLocs: Location[];
  locFilter: string;
  setLocFilter: (v: string) => void;
  harborId: string | null;
  setHarborId: (id: string) => void;
  location: Location | null;
  locBusy: boolean;
  locErr: string | null;
  /** The create-location form: App's, because it owns the form's state and the
   *  call that writes to the account. Rendered in place of the button. */
  showCreateLoc: boolean;
  /** The location came back changed: App owns the list and the selection, so
   *  it is App that puts it back. */
  onLocationUpdated: (loc: Location) => void;
  setShowCreateLoc: (v: boolean) => void;
  createLocationForm: React.ReactNode;
  // -- the agents in it
  ships: Ship[];
  shipId: string | null;
  pickShip: (id: string) => void;
  shipOnline: (s: Ship) => boolean;
  factsBusy: boolean;
  facts: Facts | null;
  creatingShip: boolean;
  setShowCreateShip: (v: boolean) => void;
  newShipName: string;
  setNewShipName: (v: string) => void;
  createShip: () => Promise<void>;
  shipErr: string | null;
  shipTokenNotice: string | null;
  // -- the credential
  authToken: string;
  setAuthToken: (v: string) => void;
  /** Issue a new one for an agent that already exists. Resolves once the token
   *  is in the field; throws with the account's own refusal if it is refused. */
  regenerateToken: () => Promise<void>;
}

/** Where the confirm has got to. Per agent, and reset by changing agent:
 *  "Regenerated" is a statement about one identity. */
type Arm = "idle" | "armed" | "done";

export function AgentPanel(p: AgentPanelProps) {
  const connected = !!p.who;
  // A pasted pair outranks the path: typing both is the deliberate act, and
  // the path field is prefilled from whatever key was detected on this machine.
  const pasted = !!p.pasteId && !!p.pasteSecret;
  const empty = !!p.location && p.ships.length === 0;
  const ship = p.ships.find((s) => s.id === p.shipId);
  // An identity that already existed, with no credential in hand for it: its
  // token was issued once, at creation, and no API reads one back. Selecting
  // the row does not rotate anything -- the button below is what does.
  const reusing = !!ship && !p.authToken;

  const [arm, setArm] = useState<Arm>("idle");
  const [issuing, setIssuing] = useState(false);
  const [issueErr, setIssueErr] = useState<string | null>(null);
  const [makingShip, setMakingShip] = useState(false);
  // Which row is open. Separate from `shipId`: closing a row is a view action
  // and must not un-choose the agent the bundle is for.
  const [open, setOpen] = useState<string | null>(null);
  // The row on its way out. Its body stays mounted for the length of the
  // transition, or switching agents collapses the old row in one frame while
  // the new one animates -- the jump the animation exists to remove.
  const [closing, setClosing] = useState<string | null>(null);
  const wasOpen = useRef<string | null>(null);

  // Disarm on every change of agent, and open that agent's row. Keyed on shipId
  // rather than on the click so the lone-agent auto-pick opens its row too, and
  // so a row closed by hand stays closed.
  useEffect(() => {
    setArm("idle"); setIssueErr(null); setOpen(p.shipId);
  }, [p.shipId]);
  useEffect(() => {
    const prev = wasOpen.current;
    wasOpen.current = open;
    if (!prev || prev === open) return;
    setClosing(prev);
    const t = setTimeout(() => setClosing(null), 200);
    return () => clearTimeout(t);
  }, [open]);

  const toggle = (id: string) => {
    if (p.shipId !== id) { p.pickShip(id); return; }
    setOpen((cur) => (cur === id ? null : id));
  };
  const regenerate = async () => {
    if (arm === "done" || issuing) return;
    if (arm === "idle") { setArm("armed"); return; }
    setIssuing(true); setIssueErr(null);
    try {
      await p.regenerateToken();
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
    try { await p.createShip(); } finally { setMakingShip(false); }
  };

  const pathSeg = (label: string, value: string | null, warn = false) => (
    <span className="flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className={"text-xs font-medium "
        + (value ? "text-slate-800" : warn ? "text-amber-700" : "text-slate-400")}>
        {value ?? (warn ? "none yet" : "—")}
      </span>
    </span>
  );

  return (
    <div className="space-y-3">
      <SegmentedControl
        value={p.sourceMode}
        onChange={(v) => p.switchMode(v as "connect" | "manual")}
        options={[
          { value: "connect", label: "Connect to BlazeMeter",
            hint: "Pick a location and agent; a new agent's token is issued once, when you create it." },
          { value: "manual", label: "Enter values manually",
            hint: "For an account you cannot reach — generation only, nothing is checked." },
        ]} />

      {p.sourceMode === "manual" ? (
        <ManualSource
          harborId={p.manual.harbor_id}
          shipId={p.manual.ship_id}
          authToken={p.authToken}
          onHarborId={(v) => p.setManual((m) => ({ ...m, harbor_id: v }))}
          onShipId={(v) => p.setManual((m) => ({ ...m, ship_id: v }))}
          onAuthToken={p.setAuthToken} />
      ) : !p.sourceOpen ? (
        /* Settled: say what was chosen, and offer the way back. Reached only by
           switching away from manual entry and back -- picking an agent no
           longer folds the step away, because in a step flow there is nothing
           underneath for it to make room for. */
        <div className="flex items-start gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
          <p className="text-xs text-slate-600 grow">
            <b>{p.location?.name ?? p.harborId}</b>
            {" · agent "}<code>{p.shipId}</code>
            <span className="block text-slate-400">
              {p.who?.email} · images: {p.facts?.images_source}
            </span>
          </p>
          <Button kind="ghost" onClick={() => p.setSourceOpen(true)}>Change</Button>
        </div>
      ) : (
        <>
          {/* One block, in one place, whatever state it is in. It used to
              swap for a single "Connected as ..." line, so connecting made the
              whole step jump and disconnecting made it jump back -- and the
              way out moved with it. The fields stay put and describe the key
              in use; the button that connected is the button that
              disconnects. */}
          <SubSection title="Connect" done={!!p.who}
            hint="API key stays on this machine; only used server-side.">
            <div className="space-y-3">
              {/* Above the path, because it is the answer to "I do not have a
                  file" and that is the question someone with no file is asking.
                  Still folded: the path is prefilled from a detected key, so
                  most sessions never open this. */}
              <details className="text-sm">
                <summary className="cursor-pointer text-slate-500">Paste a key instead</summary>
                <div className="mt-2 space-y-2">
                  <Field label="Key ID">
                    <TextInput value={p.pasteId} onChange={p.setPasteId} mono
                      disabled={connected} /></Field>
                  <Field label="Secret">
                    <input type="password"
                      className={inputCls + " font-mono text-xs"
                        + (connected ? " bg-slate-50 text-slate-500" : "")}
                      value={p.pasteSecret} disabled={connected}
                      onChange={(e) => p.setPasteSecret(e.target.value)} />
                  </Field>
                </div>
              </details>
              <div className="flex gap-2 items-end">
                <div className="grow">
                  <Field label="…or api-key.json">
                    <TextInput value={p.keyPath} onChange={p.setKeyPath} mono
                      disabled={connected}
                      placeholder="/path/to/api-key.json" />
                  </Field>
                </div>
                {/* A label, not a Button, so it cannot be `disabled` -- while a
                    connect is in flight, or one is already made, it is taken
                    out of reach instead. */}
                <label className={"rounded-md px-3 py-1.5 text-sm font-medium border "
                  + "border-slate-300 text-slate-600 whitespace-nowrap "
                  + (p.connecting || connected
                    ? "opacity-40 pointer-events-none"
                    : "hover:bg-slate-50 cursor-pointer")}>
                  Browse…
                  <input type="file" accept=".json,application/json" className="hidden"
                    onChange={async (e) => {
                      const f = e.target.files?.[0];
                      if (!f) return;
                      e.target.value = "";
                      p.setConnErr(null);
                      try {
                        const d = JSON.parse(await f.text());
                        if (!d.id || !d.secret) throw new Error();
                        p.connect({ id: d.id, secret: d.secret, save: p.saveKey });
                      } catch {
                        p.setConnErr(`${f.name} is not an api-key JSON ({"id": ..., "secret": ...})`);
                      }
                    }} />
                </label>
                {/* The same button, in the same place, doing the other half of
                    the same job. The fixed box is the point: three labels of
                    three widths in a row whose text field is `grow` would
                    resize the field under the cursor every time the state
                    changed. */}
                <div className="w-32 shrink-0">
                  {/* One button for both ways in: a pasted id and secret if
                      there is one, the file otherwise. Two Connects meant two
                      places to look for the one that was going to work, and
                      the pasted pair is the deliberate act -- if it is filled
                      in, it is what was meant. */}
                  <Button block kind={connected ? "ghost" : "primary"}
                    onClick={connected ? p.disconnect
                      : () => p.connect(pasted
                        ? { id: p.pasteId, secret: p.pasteSecret, save: p.saveKey }
                        : { path: p.keyPath })}
                    disabled={!connected && !pasted && !p.keyPath}
                    busy={p.connecting}>
                    {connected ? "Disconnect"
                      : p.connecting ? "Connecting…" : "Connect"}
                  </Button>
                </div>
              </div>
              <Check label="Remember this key on this machine" checked={p.saveKey}
                onChange={p.setSaveKey} disabled={connected}
                hint="Browse & paste only — saved to ~/.config/bzm-opl-gen/api-key.json (chmod 600)" />
              {/* Status and failure share the slot under the form, so neither
                  arriving moves anything. */}
              {connected
                ? <p className="text-sm text-emerald-700">Connected as {p.who!.email}</p>
                : <ErrorMsg msg={p.connErr} />}
            </div>
          </SubSection>

          {/* Where you are, always, and what is still missing. A location with
              no agent says so here rather than in a panel further down that you
              have to reach before the absence is visible. */}
          <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 flex items-center gap-2 flex-wrap">
            {pathSeg("location", p.location?.name ?? null)}
            <span className="text-slate-300">›</span>
            {pathSeg("agent", ship?.name ?? null, !!p.location)}
            {empty && (
              <span className="text-[11px] text-amber-700 ml-1">
                — this location is empty; the first agent has to be created
              </span>
            )}
          </div>

          <SubSection title="Private location" done={!!p.harborId}
            hint="A location holds agents.">
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <Field label="Account">
                  <SearchSelect
                    options={p.accounts.map((a) => ({ value: a.id, label: `${a.name} (${a.id})` }))}
                    value={p.accountId} disabled={!p.who}
                    onChange={(v) => p.setAccountId(Number(v))} />
                </Field>
                <Field label="Workspace">
                  <SearchSelect
                    options={p.workspaces.map((w) => ({ value: w.id, label: w.name }))}
                    value={p.workspaceId} disabled={!p.who || p.workspaces.length === 0}
                    onChange={(v) => p.setWorkspaceId(Number(v))} />
                </Field>
              </div>
              {/* Above the list, like the agent panel below: the two read the
                  same way down the page -- make one, or choose one. */}
              {p.showCreateLoc ? p.createLocationForm : (
                <>
                  <ErrorMsg msg={p.locErr} />
                  <Button kind="ghost" disabled={!p.who}
                    onClick={() => p.setShowCreateLoc(true)}>
                    + New location (new harbor_id)
                  </Button>
                </>
              )}
              {p.locations.length > 8 && (
                <TextInput value={p.locFilter} onChange={p.setLocFilter}
                  placeholder={`filter ${p.locations.length} locations…`} />
              )}
              {p.locBusy && (
                <p className="flex items-center gap-2 text-xs text-slate-500">
                  <Spinner className="text-bzm" /> reading this workspace&apos;s locations…
                </p>
              )}
              {/* A list has to look like one: zebra banding and a divider a
                  shade darker than the card's own border. Rows that share a
                  background and a hairline read as one block of text. */}
              <div className={"max-h-56 overflow-y-auto border border-slate-300 rounded-md divide-y divide-slate-200 "
                + (p.locBusy ? "opacity-40" : "")}>
                {p.filteredLocs.map((l, i) => {
                  const n = (l.ships ?? []).length;
                  const up = (l.ships ?? []).filter(p.shipOnline).length;
                  return (
                    <button key={l.id} onClick={() => p.setHarborId(l.id)}
                      className={"w-full text-left px-3 py-2.5 text-sm hover:bg-slate-100 flex items-center gap-2 "
                        + (l.id === p.harborId ? "bg-bzm/10 border-l-4 border-bzm"
                          : i % 2 ? "bg-slate-50/70" : "bg-white")}>
                      <span className={"h-1.5 w-1.5 rounded-full shrink-0 "
                        + (n ? "bg-emerald-500" : "bg-amber-400")} />
                      <span className="font-medium">{l.name}</span>
                      <span className="text-xs text-slate-400 truncate">
                        {l.slots} slot{l.slots === 1 ? "" : "s"}
                      </span>
                      <span className="grow" />
                      <span className={"text-[11px] " + (n ? "text-slate-500" : "text-amber-700")}>
                        {n ? `${n} agent${n === 1 ? "" : "s"}${up ? ` · ${up} online` : ""}`
                           : "no agents yet"}
                      </span>
                    </button>
                  );
                })}
                {!!p.who && p.filteredLocs.length === 0 && !p.locBusy && (
                  <p className="px-3 py-2 text-sm text-slate-400">no locations match</p>
                )}
              </div>
              {/* Under the list, for the location that is selected: this is a
                  change to something that exists, so it belongs where the thing
                  it changes is, rather than in a settings screen of its own. */}
              {p.location && (
                <LocationSettings location={p.location}
                  onUpdated={p.onLocationUpdated} />
              )}
            </div>
          </SubSection>

          <SubSection title="Agent (ship)" done={!!p.shipId}
            hint="One agent = one deployment, inside the location above.">
            <div className="space-y-3">
              {p.factsBusy && (
                <p className="flex items-center gap-2 text-xs text-slate-500">
                  <Spinner className="text-bzm" /> reading this location&apos;s agents…
                </p>
              )}
              {!p.factsBusy && !p.location && (
                <p className="text-xs text-slate-400">Pick a location above first.</p>
              )}
              {!p.factsBusy && empty && (
                <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5">
                  <p className="text-xs text-amber-900">
                    <b>{p.location!.name}</b> has no agents yet — nothing is
                    deployed to it.
                  </p>
                  <p className="text-[11px] text-amber-700 mt-0.5">
                    Create the first one below; its AUTH_TOKEN is issued then, once.
                  </p>
                </div>
              )}
              {!p.factsBusy && p.location && (
                <>
                  {p.creatingShip ? (
                    <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
                      <p className="text-xs font-semibold text-slate-700">
                        {empty ? "Create the first agent in this location"
                               : "New agent in this location"}
                      </p>
                      <Field label="Name">
                        <TextInput value={p.newShipName} onChange={p.setNewShipName}
                          placeholder="e.g. k8s-prod-cluster" />
                      </Field>
                      <div className="flex gap-2 items-center">
                        {/* Creating an agent is a round trip that also issues
                            its token; the button says so while it waits rather
                            than looking ignored, which is how a second click --
                            and a second agent -- happens. */}
                        <Button disabled={!p.harborId || !p.newShipName}
                          busy={makingShip} onClick={createShip}>
                          {makingShip ? "Creating…" : "Create"}
                        </Button>
                        {p.ships.length > 0 && !makingShip && (
                          <Button kind="ghost" onClick={() => p.setShowCreateShip(false)}>
                            Cancel
                          </Button>
                        )}
                      </div>
                    </div>
                  ) : (
                    <Button kind="ghost" onClick={() => p.setShowCreateShip(true)}>
                      + New agent identity (recommended)
                    </Button>
                  )}

                  {p.ships.length > 0 && (
                    <div className="border border-slate-300 rounded-md divide-y divide-slate-200">
                      {p.ships.map((s, i) => {
                        const up = p.shipOnline(s);
                        const on = s.id === p.shipId;
                        const isOpen = open === s.id;
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
                              {on && (p.authToken || arm === "done") && (
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
                                      {reusing && arm === "idle" && !issuing && (
                                        <span className="text-[11px] text-slate-500">
                                          its token was issued once, at creation,
                                          and cannot be read back
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
              <ErrorMsg msg={p.shipErr} />
              <NoticeMsg msg={p.shipTokenNotice} />
              {p.facts && (
                <p className="text-xs text-slate-500">
                  image inventory: {p.facts.images_source} · features:{" "}
                  {p.facts.func_ids?.join(", ")}
                </p>
              )}
            </div>
          </SubSection>
        </>
      )}
    </div>
  );
}
