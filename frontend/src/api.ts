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
/** Manual facts, plus the one thing no catalogue can supply. `func_ids`
 *  including functionalGui means the bundle needs a version-pinned browser
 *  image (charmander/chrome_*, firefox_*, …) that only a live agent inventory
 *  names -- harmless against the public registry, a gap against a private one. */
export interface ManualFactsOut { facts: Facts; gui_images_incomplete: boolean }
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
  /** Facts from the three values BlazeMeter shows on the agent, with no API key.
   *  Nothing is validated and nothing is looked up -- see /api/facts/manual. */
  manualFacts: (body: { harbor_id: string; ship_id: string; func_ids: string[] }) =>
    req<ManualFactsOut>("POST", "/api/facts/manual", body),
  status: (harborId: string, shipId: string) =>
    req<AgentStatus>("GET", `/api/status?harbor_id=${harborId}&ship_id=${shipId}`),
  generate: (facts: Facts, options: Options) =>
    req<{ files: GeneratedFile[] }>("POST", "/api/generate",
      { facts, options, fetch_token: false }),
  profiles: () => req<{ name: string; options: Options }[]>("GET", "/api/profiles"),
  optionDefaults: () => req<Options>("GET", "/api/option-defaults"),
  funcIdChoices: () => req<FuncIdChoice[]>("GET", "/api/func-ids"),
  features: () => req<Feature[]>("GET", "/api/features"),
  svConstants: () => req<SvConstants>("GET", "/api/sv-constants"),
  svMocks: (namespace: string, subdomain: string) =>
    req<SvMocksOut>("GET", "/api/sv-mocks?" + new URLSearchParams(
      subdomain ? { namespace, sv_subdomain: subdomain } : { namespace })),
  svCheck: (host: string, scheme: SvScheme) =>
    req<SvCheckOut>("GET", "/api/sv-check?" + new URLSearchParams({ host, scheme })),
};

/** Served rather than declared here: generate.py owns both lists, and a copy in
 *  TypeScript is how a new expose backend goes missing from the picker. */
export interface SvBackend {
  group: string;
  resources: string[];
  /** What crane publishes with it — "Ingress", "Gateway + VirtualService", … */
  creates: string;
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

/** How reading the namespace ended. The watch panel is the only thing in this
 *  client that needs a cluster, and the only one allowed to: cluster access is
 *  optional, so an unreadable one is an "ok" HTTP response carrying which of
 *  the four reasons it was, and the caller never has to guess from an error
 *  string. */
export type SvReadStatus = "ok" | "no_cli" | "no_context" | "denied" | "no_mocks";

/** One feature the configure step can be pointed at, from /api/features. The
 *  list is served for the same reason as the two above -- functional testing,
 *  secrets and API monitoring are expected to follow, and a feature has to
 *  become selectable by being added to the vocabulary, not by an edit here.
 *  Option groups tag themselves with `id` (see optionGroups.ts); nothing in the
 *  frontend enumerates the features themselves. */
export interface Feature {
  id: string;
  label: string;
  hint?: string;
  /** Suggested, never forced: applied only while the namespace field still
   *  holds a namespace some feature suggested. Served with the label so the
   *  suggestion extends with the vocabulary. */
  namespace: string;
  /** The location funcIds that mean a location has this feature. A location's
   *  funcIds may include ones no feature claims -- that is not an error. */
  func_ids: string[];
}

/** A deployed virtual service and the host it answers at. `host` is null until
 *  a wildcard domain is configured. Built by the generator and carried here, so
 *  no caller rebuilds the string the endpoint is published at. */
export interface SvEndpoint { name: string; port: number; host: string | null }

/** What is deployed right now, for the watch panel. */
export interface SvMocksOut {
  status: SvReadStatus;
  mocks: SvEndpoint[];
  message: string;
}
/** Whether the endpoint a deployed mock publishes actually answers, probed by
 *  the machine serving this page. `status` is how the attempt ended, not how
 *  the mock feels: "ok" means something replied with a status line — a 503
 *  included, and that one is the finding, not a failed check. Its `message`
 *  carries the sv-expose wording, so no caller has to recognise the code. */
export type SvCheckStatus = "ok" | "dns" | "refused" | "tls" | "timeout" | "error";
export type SvScheme = "http" | "https";
export interface SvCheckOut {
  status: SvCheckStatus;
  /** The HTTP status, or null when nothing answered with one. */
  code: number | null;
  url: string;
  message: string;
  /** The raw reason, for the cases where the sentence above is not enough. */
  detail: string;
}
/** Save a Blob to disk under `filename`. Named rather than inlined into
 *  downloadZip because the object-URL and anchor dance is where the browser
 *  quirks live, and it is worth having one place to fix them. */
export function saveBlob(blob: Blob, filename: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** `fetchToken` pulls AUTH_TOKEN from the API on the way out, which is what the
 *  connected flow relies on. It must be off for manually-entered facts: the
 *  token is already in `options`, and a stale key left over from an earlier
 *  connect would otherwise be asked for a token for someone else's agent. */
export async function downloadZip(facts: Facts, options: Options, fetchToken = true) {
  const r = await fetch("/api/generate/zip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ facts, options, fetch_token: fetchToken }),
  });
  if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
  saveBlob(await r.blob(),
    `bzm-opl-${(options.namespace as string) || "blazemeter"}.zip`);
}
