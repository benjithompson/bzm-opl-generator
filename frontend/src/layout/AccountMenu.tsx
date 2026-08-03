// Who this session is, at the foot of the nav drawer: the key, the account it
// can see, and the workspace inside that account -- one control, because they
// are one answer narrowed three times and each only means anything given the
// one before it.
//
// Connecting used to be the first section of step 1, and the account and
// workspace were fields in the Private location panel below it. That put "which
// API key am I using" inside "which agent am I generating for" -- two questions
// of different lifetimes -- and left a selection made in step 1 quietly
// deciding what a different view showed, since the location list, the agent
// under it and the whole Account capacity view all read the account. A key
// lasts the session; an agent is chosen per bundle. So all three live in the
// chrome, and step 1 starts at the location.
//
// The menu says the state and holds the two pickers; the form for a *new* key
// is a modal, because connecting is a question being asked rather than a panel
// to work in. The menu opens upward, since it is the last thing in the drawer,
// and grows into the workspace once there is an account to have one -- an empty
// workspace picker above an unchosen account is a control for a question that
// has not been asked.
import { useEffect, useMemo, useRef, useState } from "react";

import { Account, Workspace } from "../api";
import {
  Button, Check, ErrorMsg, Field, Modal, SearchSelect, SecretInput, Spinner,
  TextInput,
} from "../components";

export interface ConnectProps {
  who: { email: string; keyId: string } | null;
  disconnect: () => void;
  // -- what the key can see. Owned by App, like everything else here.
  accounts: Account[];
  accountId: number | null;
  setAccountId: (id: number | null) => void;
  accountsBusy: boolean;
  workspaces: Workspace[];
  workspaceId: number | null;
  setWorkspaceId: (id: number | null) => void;
  workspacesBusy: boolean;
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
  /** The drawer is a rail: the status dot alone, with the email as its
   *  tooltip. Whether there is a key is the one thing that still has to be
   *  legible at 56 pixels. */
  collapsed?: boolean;
}

/** How long the workspace row takes to grow in. Kept beside the `duration-200`
 *  the row is actually animated with -- the two have to agree, and the class is
 *  a literal because Tailwind reads the source rather than the value. */
const GROW_MS = 200;

export function AccountMenu(p: ConnectProps) {
  const [menu, setMenu] = useState(false);
  const [form, setForm] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const connected = !!p.who;

  // A menu closes when you look elsewhere; the modal it opens does not.
  //
  // Against the event's own path, not `root.contains(e.target)`. The two agree
  // on every click except the ones this menu is made of: picking an option
  // commits on *mousedown*, and the list unmounts in that same handler, so by
  // the time a document-level listener runs its target is a node with no parent
  // -- `contains` says false, and choosing an account shut the menu you were
  // choosing a workspace in. `composedPath()` is taken when the event is
  // dispatched, so it still holds the ancestors the click actually went
  // through. Falling back to `contains` covers a dispatch that carries no path
  // (older jsdom, synthetic events in tests).
  useEffect(() => {
    if (!menu) return;
    const h = (e: MouseEvent) => {
      const el = root.current;
      if (!el) return;
      const path = typeof e.composedPath === "function" ? e.composedPath() : [];
      const inside = path.length
        ? path.includes(el) : el.contains(e.target as Node);
      if (!inside) setMenu(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [menu]);

  // The form closes itself once the key is accepted: `who` arriving is the only
  // signal that the connect worked, and leaving the modal up over a connected
  // page would make the user close the thing that had just succeeded.
  useEffect(() => { if (connected) setForm(false); }, [connected]);

  const pasted = !!(p.pasteId && p.pasteSecret);
  const account = p.accounts.find((a) => a.id === p.accountId) ?? null;
  // Built once per list rather than per render. This menu re-renders whenever
  // anything in App does, and a fresh array each time re-filters 166 workspace
  // options inside SearchSelect for a keystroke in an unrelated field.
  const accountOpts = useMemo(
    () => p.accounts.map((a) => ({ value: a.id, label: `${a.name} (${a.id})` })),
    [p.accounts]);
  const workspaceOpts = useMemo(
    () => p.workspaces.map((w) => ({ value: w.id, label: w.name })),
    [p.workspaces]);
  const workspace = p.workspaces.find((w) => w.id === p.workspaceId) ?? null;
  // The workspace question only exists once an account has been chosen: without
  // one there is no list to choose from, and a disabled picker sitting there
  // reads as a step that has been skipped rather than one not yet reached.
  const askWorkspace = connected && p.accountId != null;

  // The clip that makes the row grow in is also a clip on anything that has to
  // *leave* it, and the workspace picker's list is absolutely positioned: it
  // was being cut to the height of the field it hangs off, which showed one row
  // of a 166-workspace account. So the overflow is hidden only while the height
  // is moving. On a timer rather than transitionend, because an animation that
  // does not run -- reduced motion, a browser that skips it -- fires no event,
  // and the failure that leaves is the one being fixed here.
  const [clip, setClip] = useState(!askWorkspace);
  useEffect(() => {
    if (!askWorkspace) { setClip(true); return; }
    const t = window.setTimeout(() => setClip(false), GROW_MS + 20);
    return () => window.clearTimeout(t);
  }, [askWorkspace]);

  return (
    <div ref={root} className="relative">
      <button onClick={() => setMenu(!menu)} aria-expanded={menu}
        title={connected ? `${p.who!.email} — the key everything is read with`
          : "not connected — no account is being read"}
        className={"flex items-center gap-2 rounded-md border text-xs w-full "
          + "transition-colors "
          + (p.collapsed ? "justify-center px-0 py-2 " : "px-2.5 py-1.5 ")
          + (connected
            ? "border-slate-300 text-slate-700 hover:bg-slate-100"
            : "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100")}>
        <span className={"rounded-full shrink-0 "
          + (p.collapsed ? "h-2.5 w-2.5 " : "h-1.5 w-1.5 ")
          + (connected ? "bg-emerald-500" : "bg-amber-400")} />
        {!p.collapsed && (
          <>
            {/* The email, and under it where in the account the page is
                pointed. Two lines because they answer two questions and the
                second one changes far more often than the first. */}
            <span className="grow min-w-0 text-left">
              <span className="font-medium truncate block">
                {connected ? p.who!.email : "Not connected"}
              </span>
              {connected && (
                <span className={"block truncate text-[10px] "
                  + (account ? "text-slate-400" : "text-amber-700")}>
                  {account
                    ? account.name + (workspace ? ` · ${workspace.name}` : "")
                    : "no account chosen"}
                </span>
              )}
            </span>
            <svg viewBox="0 0 20 20" className="w-3.5 h-3.5 shrink-0" fill="none"
              stroke="currentColor" strokeWidth={1.75}
              strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12l5-5 5 5" />
            </svg>
          </>
        )}
      </button>

      {menu && (
        <div className="absolute bottom-full left-0 mb-1 w-96 z-50 bg-white
                        border border-slate-200 rounded-lg shadow-lg p-2">
          {/* The way out, said rather than only implied. Clicking away still
              closes -- that is what a menu does -- but this one is worked in
              rather than glanced at: two pickers, a search in each, and a list
              that can be 166 long. Something you spend a minute inside should
              not leave "click somewhere else" as its only exit. */}
          <button type="button" onClick={() => setMenu(false)} aria-label="Close"
            className="absolute top-1.5 right-1.5 w-6 h-6 rounded text-slate-400
                       hover:text-slate-800 hover:bg-slate-100 flex items-center
                       justify-center text-sm leading-none">
            ✕
          </button>
          <div className="px-2 py-1.5">
            {/* Room kept for the button, so a long address wraps beside it
                rather than under it. */}
            <p className="text-[11px] uppercase tracking-wide text-slate-400 font-semibold pr-6">
              Connected as
            </p>
            {connected ? (
              <>
                <p className="text-xs text-slate-800 mt-0.5 break-all">{p.who!.email}</p>
                <p className="text-[11px] text-slate-400 break-all">
                  key {p.who!.keyId.slice(0, 12)}…
                </p>
              </>
            ) : (
              <p className="text-xs text-slate-500 mt-0.5">
                Nothing is read from BlazeMeter until a key is connected.
                Manifests can still be generated by hand.
              </p>
            )}
          </div>

          {/* Inside the key, because they are what the key can see. Both are
              the whole session's: the location list, the agent under it and
              Account capacity all read them. */}
          {connected && (
            <div className="border-t border-slate-100 mt-1 pt-2 px-2 pb-1 space-y-2">
              <Field label="Account">
                <SearchSelect
                  options={accountOpts}
                  value={p.accountId} busy={p.accountsBusy}
                  onChange={(v) => p.setAccountId(Number(v))}
                  onClear={() => p.setAccountId(null)} />
              </Field>
              {/* Grows in on the same 0fr -> 1fr grid the rest of the page
                  expands on, so choosing an account opens the next question
                  rather than making the menu jump to a new height. */}
              <div className={"grid transition-[grid-template-rows] duration-200 "
                + "ease-out " + (askWorkspace ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
                <div className={clip ? "overflow-hidden" : ""}>
                  <Field label="Workspace"
                    hint="the locations in step 1 are this workspace's">
                    <SearchSelect
                      options={workspaceOpts}
                      value={p.workspaceId} busy={p.workspacesBusy}
                      disabled={!p.workspacesBusy && p.workspaces.length === 0}
                      onChange={(v) => p.setWorkspaceId(Number(v))}
                      onClear={() => p.setWorkspaceId(null)} />
                  </Field>
                </div>
              </div>
              {!account && (
                <p className="text-[11px] text-amber-700">
                  Choose an account: without one there are no locations to pick
                  from and nothing for Account capacity to add up.
                </p>
              )}
            </div>
          )}

          <div className="border-t border-slate-100 mt-1 pt-1 space-y-0.5">
            <MenuItem onClick={() => { setMenu(false); setForm(true); }}>
              {connected ? "Use a different key…" : "Connect…"}
            </MenuItem>
            {connected && (
              <MenuItem danger onClick={() => { setMenu(false); p.disconnect(); }}>
                Disconnect
              </MenuItem>
            )}
          </div>
          {connected && (
            <p className="px-2 pt-1.5 text-[11px] text-slate-400">
              Disconnecting forgets the key here. One saved to disk stays there.
            </p>
          )}
        </div>
      )}

      <Modal open={form} onClose={() => { setForm(false); p.setConnErr(null); }}
        title="Connect to BlazeMeter"
        hint="the key stays on this machine; only used server-side">
        <div className="space-y-3">
          {/* Above the path, because it is the answer to "I do not have a file"
              and that is the question someone with no file is asking. Folded:
              the path is prefilled from a detected key, so most sessions never
              open this. */}
          <details className="text-sm">
            <summary className="cursor-pointer text-slate-500">Paste a key instead</summary>
            <div className="mt-2 space-y-2">
              <Field label="Key ID">
                <TextInput value={p.pasteId} onChange={p.setPasteId} mono
                  disabled={connected} /></Field>
              <Field label="Secret">
                {/* The page's masked-credential control, not a hand-built
                    type=password: this one gets the same Show/Hide as the
                    AUTH_TOKEN field, which is the other secret on the page. */}
                <SecretInput value={p.pasteSecret}
                  onChange={p.setPasteSecret} />
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
                connect is in flight it is taken out of reach instead. */}
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
          </div>

          <Check label="Remember this key on this machine" checked={p.saveKey}
            onChange={p.setSaveKey} disabled={connected}
            hint="Browse & paste only — saved to ~/.config/bzm-opl-gen/api-key.json (chmod 600)" />

          <div className="flex items-center gap-2">
            {/* One button for both ways in: a pasted id and secret if there is
                one, the file otherwise. The pasted pair is the deliberate act --
                if it is filled in, it is what was meant. */}
            <Button
              onClick={() => p.connect(pasted
                ? { id: p.pasteId, secret: p.pasteSecret, save: p.saveKey }
                : { path: p.keyPath })}
              disabled={connected || (!pasted && !p.keyPath)}
              busy={p.connecting}>
              {p.connecting ? "Connecting…" : "Connect"}
            </Button>
            {p.connecting && (
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <Spinner className="text-bzm" /> asking BlazeMeter who this key is
              </span>
            )}
          </div>
          <ErrorMsg msg={p.connErr} />
        </div>
      </Modal>
    </div>
  );
}

function MenuItem(props: {
  onClick: () => void; children: React.ReactNode; danger?: boolean;
}) {
  return (
    <button onClick={props.onClick}
      className={"w-full text-left px-2 py-1.5 rounded text-xs font-medium "
        + (props.danger
          ? "text-red-700 hover:bg-red-50"
          : "text-slate-700 hover:bg-slate-100")}>
      {props.children}
    </button>
  );
}
