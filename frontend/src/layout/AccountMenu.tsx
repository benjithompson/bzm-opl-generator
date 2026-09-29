// The key, the account it can see and the workspace inside it, at the foot of
// the nav drawer: they last the session, while an agent is chosen per bundle.
// The menu opens upward and shows the workspace picker once an account is
// chosen; connecting a new key is a modal. The paste form and its refusal are
// kept here.
import { useEffect, useMemo, useRef, useState } from "react";

import { Account, Workspace } from "../api";
import {
  Button, Check, ErrorMsg, Field, Modal, SearchSelect, SecretInput, Spinner,
  TextInput,
} from "../components";

export interface ConnectBody { path?: string; id?: string; secret?: string; save?: boolean }

interface ConnectProps {
  who: { email: string; keyId: string } | null;
  disconnect: () => void;
  // -- what the key can see, owned by App.
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
  /** Resolves with the refusal to show, or null. */
  connect: (body: ConnectBody) => Promise<string | null>;
  connecting: boolean;
  /** The drawer is collapsed to a rail: show the status dot alone, the email
   *  as its tooltip. */
  collapsed?: boolean;
}

/** How long the workspace row takes to grow in; must match its `duration-200`. */
const GROW_MS = 200;

export function AccountMenu(p: ConnectProps) {
  const [menu, setMenu] = useState(false);
  const [form, setForm] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const connected = !!p.who;
  const [pasteId, setPasteId] = useState("");
  const [pasteSecret, setPasteSecret] = useState("");
  const [saveKey, setSaveKey] = useState(false);
  const [connErr, setConnErr] = useState<string | null>(null);
  const connect = async (body: ConnectBody) => {
    setConnErr(null);
    setConnErr(await p.connect(body));
  };

  // Clicking elsewhere closes the menu (not the modal). Checked against the
  // event's path: an option unmounts on mousedown, so `contains` would say the
  // click was outside. Falls back to `contains` when there is no path.
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

  // Close the form once the key is accepted.
  useEffect(() => { if (connected) setForm(false); }, [connected]);

  const pasted = !!(pasteId && pasteSecret);
  const account = p.accounts.find((a) => a.id === p.accountId) ?? null;
  // Memoised: the menu re-renders with App, and a new array re-filters the
  // whole list inside SearchSelect.
  const accountOpts = useMemo(
    () => p.accounts.map((a) => ({ value: a.id, label: `${a.name} (${a.id})` })),
    [p.accounts]);
  const workspaceOpts = useMemo(
    () => p.workspaces.map((w) => ({ value: w.id, label: w.name })),
    [p.workspaces]);
  const workspace = p.workspaces.find((w) => w.id === p.workspaceId) ?? null;
  // The workspace question exists only once an account is chosen.
  const askWorkspace = connected && p.accountId != null;

  // Clip only while the row is growing, or the workspace list (absolutely
  // positioned) is cut off. A timer, since a skipped animation fires no event.
  const [clip, setClip] = useState(!askWorkspace);
  useEffect(() => {
    if (!askWorkspace) { setClip(true); return; }
    const t = window.setTimeout(() => setClip(false), GROW_MS + 20);
    return () => window.clearTimeout(t);
  }, [askWorkspace]);

  return (
    <div ref={root} className="relative">
      <button type="button" onClick={() => setMenu(!menu)} aria-expanded={menu}
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
            {/* The email, and under it the account and workspace. */}
            <span className="grow min-w-0 text-left">
              <span className="font-medium truncate block">
                {connected ? p.who!.email : "Not connected"}
              </span>
              {connected && (
                <span className={"block truncate text-3xs "
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
          {/* An explicit way out: this menu is worked in, not glanced at. */}
          <button type="button" onClick={() => setMenu(false)} aria-label="Close"
            className="absolute top-1.5 right-1.5 w-6 h-6 rounded text-slate-400
                       hover:text-slate-800 hover:bg-slate-100 flex items-center
                       justify-center text-sm leading-none">
            ✕
          </button>
          <div className="px-2 py-1.5">
            {/* Room for the close button. */}
            <p className="text-2xs uppercase tracking-wide text-slate-400 font-semibold pr-6">
              Connected as
            </p>
            {connected ? (
              <>
                <p className="text-xs text-slate-800 mt-0.5 break-all">{p.who!.email}</p>
                <p className="text-2xs text-slate-400 break-all">
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

          {/* The account and workspace every view reads. */}
          {connected && (
            <div className="border-t border-slate-100 mt-1 pt-2 px-2 pb-1 space-y-2">
              <Field label="Account">
                <SearchSelect
                  options={accountOpts}
                  value={p.accountId} busy={p.accountsBusy}
                  onChange={(v) => p.setAccountId(Number(v))}
                  onClear={() => p.setAccountId(null)} />
              </Field>
              {/* Grows in, so choosing an account opens the next question
                  without a jump. */}
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
                <p className="text-2xs text-amber-700">
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
            <p className="px-2 pt-1.5 text-2xs text-slate-400">
              Disconnecting forgets the key here. One saved to disk stays there.
            </p>
          )}
        </div>
      )}

      <Modal open={form} onClose={() => { setForm(false); setConnErr(null); }}
        title="Connect to BlazeMeter"
        hint="the key stays on this machine; only used server-side">
        <div className="space-y-3">
          {/* For somebody with no key file; folded, since the path is usually
              detected. */}
          <details className="text-sm">
            <summary className="cursor-pointer text-slate-500">Paste a key instead</summary>
            <div className="mt-2 space-y-2">
              <Field label="Key ID">
                <TextInput value={pasteId} onChange={setPasteId} mono
                  disabled={connected} /></Field>
              <Field label="Secret">
                {/* The same masked control as the AUTH_TOKEN field. */}
                <SecretInput value={pasteSecret}
                  onChange={setPasteSecret} />
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
            {/* A label cannot be disabled, so it is taken out of reach instead. */}
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
                  setConnErr(null);
                  try {
                    const d = JSON.parse(await f.text());
                    if (!d.id || !d.secret) throw new Error();
                    connect({ id: d.id, secret: d.secret, save: saveKey });
                  } catch {
                    setConnErr(`${f.name} is not an api-key JSON ({"id": ..., "secret": ...})`);
                  }
                }} />
            </label>
          </div>

          <Check label="Remember this key on this machine" checked={saveKey}
            onChange={setSaveKey} disabled={connected}
            hint="Browse & paste only — saved to ~/.config/bzm-opl-gen/api-key.json (chmod 600)" />

          <div className="flex items-center gap-2">
            {/* One button: the pasted pair if filled in, the file otherwise. */}
            <Button
              onClick={() => connect(pasted
                ? { id: pasteId, secret: pasteSecret, save: saveKey }
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
          <ErrorMsg msg={connErr} />
        </div>
      </Modal>
    </div>
  );
}

function MenuItem(props: {
  onClick: () => void; children: React.ReactNode; danger?: boolean;
}) {
  return (
    <button type="button" onClick={props.onClick}
      className={"w-full text-left px-2 py-1.5 rounded text-xs font-medium "
        + (props.danger
          ? "text-red-700 hover:bg-red-50"
          : "text-slate-700 hover:bg-slate-100")}>
      {props.children}
    </button>
  );
}
