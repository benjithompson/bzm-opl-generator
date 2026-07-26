// Thin typed client for the local bzm-opl-gen API.

export interface KeyCandidate { path: string; key_id: string }
export interface Account { id: number; name: string }
export interface Workspace { id: number; name: string }
export interface Ship {
  id: string; name: string; state: string;
  lastHeartBeat?: number; installedVersion?: string;
}
export interface Location {
  id: string; name: string; funcIds?: string[]; slots?: number; ships?: Ship[];
  workspacesId?: number[];
}
export interface Facts {
  harbor_id: string; harbor_name?: string; func_ids?: string[];
  ships: { id: string; name?: string }[];
  images: object[]; images_source?: string; crane_image?: string;
}
export interface GeneratedFile { name: string; content: string }
export interface AgentStatus {
  state: string; heartbeat_age_s: number | null;
  installed_version?: string; online: boolean;
}
export interface Options { [k: string]: unknown }

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.json();
}

export const api = {
  keyDetect: () =>
    req<{ candidates: KeyCandidate[]; active_key_id: string | null }>("GET", "/api/key/detect"),
  keySet: (body: { path?: string; id?: string; secret?: string; save?: boolean }) =>
    req<{ user: { email: string }; default_account_id: number | null; key_id: string }>(
      "POST", "/api/key", body),
  accounts: () => req<Account[]>("GET", "/api/accounts"),
  workspaces: (accountId: number) =>
    req<Workspace[]>("GET", `/api/workspaces?account_id=${accountId}`),
  locations: (workspaceId: number) =>
    req<Location[]>("GET", `/api/locations?workspace_id=${workspaceId}`),
  createLocation: (body: {
    name: string; account_id: number; workspace_id: number;
    func_ids: string[]; slots: number; threads_per_engine: number;
  }) => req<Location>("POST", "/api/locations", body),
  createShip: (harborId: string, name: string) =>
    req<{ ship: Ship }>("POST", "/api/ships", { harbor_id: harborId, name }),
  facts: (harborId: string) => req<Facts>("GET", `/api/facts?harbor_id=${harborId}`),
  status: (harborId: string, shipId: string) =>
    req<AgentStatus>("GET", `/api/status?harbor_id=${harborId}&ship_id=${shipId}`),
  generate: (facts: Facts, options: Options) =>
    req<{ files: GeneratedFile[] }>("POST", "/api/generate",
      { facts, options, fetch_token: false }),
  profiles: () => req<{ name: string; options: Options }[]>("GET", "/api/profiles"),
  optionDefaults: () => req<Options>("GET", "/api/option-defaults"),
  funcIdChoices: () => req<FuncIdChoice[]>("GET", "/api/func-ids"),
  svConstants: () => req<SvConstants>("GET", "/api/sv-constants"),
  svExpose: (body: SvExposeIn) => req<SvExposeOut>("POST", "/api/sv-expose", body),
};

/** Served rather than declared here: generate.py owns both lists, and a copy in
 *  TypeScript is how a new expose backend goes missing from the picker. */
export interface SvBackend {
  group: string;
  resources: string[];
  /** What crane publishes with it — "Ingress", "Gateway + VirtualService", … */
  creates: string;
  via_ingress_class: boolean;
}
export type SvConstants = {
  func_ids: string[];
  ingress_types: string[];
  backends: Record<string, SvBackend>;
};

/** Likewise served: facts.CATEGORY_BY_FUNC owns the vocabulary, and the copy
 *  that used to live here is how sv-bridge went missing from the create form.
 *  `label` falls back to the raw id server-side, so an unlabelled funcId is
 *  offered rather than dropped. */
export type FuncIdChoice = { id: string; label: string };

/** sv-expose reads the deployed mocks off a live namespace — the only call in
 *  this client that needs a cluster, and the only one allowed to. Cluster
 *  access is optional: an unreadable cluster is an "ok" HTTP response carrying
 *  which of the four reasons it was, so the caller never has to guess from an
 *  error string. `files` is the same shape /api/generate returns, so the same
 *  preview pane renders it. */
export type SvExposeStatus = "ok" | "no_cli" | "no_context" | "denied" | "no_mocks";
export interface SvMock { name: string; port: number; harbor: string; ship: string }
export interface SvExposeIn {
  namespace: string;
  sv_subdomain?: string | null;
  sv_tls_secret?: string | null;
  sv_ingress_class?: string | null;
}
export interface SvExposeOut {
  status: SvExposeStatus;
  mocks: SvMock[];
  files: GeneratedFile[];
  message: string;
  detail: string;
  /** The equivalent `bzm-opl-gen sv-expose …`, prefilled — the way forward
   *  whenever this machine cannot reach the cluster. */
  command: string;
}

export async function downloadZip(facts: Facts, options: Options) {
  const r = await fetch("/api/generate/zip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ facts, options, fetch_token: true }),
  });
  if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `bzm-opl-${(options.namespace as string) || "blazemeter"}.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
}
