// Thin typed client for the local bzm-opl-gen API.

interface KeyCandidate { path: string; key_id: string }
export interface Account { id: number; name: string }
export interface Workspace { id: number; name: string }
export interface Ship {
  id: string; name: string; state: string;
  lastHeartBeat?: number; installedVersion?: string;
}
export interface Location {
  id: string; name: string; funcIds?: string[]; slots?: number; ships?: Ship[];
  workspacesId?: number[];
  /** The concurrency settings beyond `slots`, in BlazeMeter's names.
   *  `overrideCPU`/`overrideMemory` are the engine pod's requests (memory in
   *  MB); null means crane's 250m/256Mi. */
  threadsPerEngine?: number | null;
  overrideCPU?: number | null;
  overrideMemory?: number | null;
}

/** The four settings this tool will change, as it names them. */
export interface LocationSettings {
  slots: number | null;
  threads_per_engine: number | null;
  override_cpu: number | null;
  override_memory: number | null;
}

/** The answer to a settings change: what the account holds afterwards.
 *  `ignored` is what was sent and did not change, which happens: BlazeMeter
 *  accepts `threadsPerEngine` and drops it. */
export interface LocationUpdate {
  location: Location;
  changed: Partial<LocationSettings>;
  ignored: string[];
  before: LocationSettings;
  after: LocationSettings;
}
export interface Facts {
  harbor_id: string; harbor_name?: string; func_ids?: string[];
  ships: { id: string; name?: string }[];
  images: object[]; images_source?: string; crane_image?: string;
}
export interface GeneratedFile { name: string; content: string }
/** Manual facts. `gui_images_incomplete` is served but not read here: the page
 *  cannot declare functionalGui in manual entry. */
export interface ManualFactsOut { facts: Facts; gui_images_incomplete: boolean }
export interface AgentStatus {
  state: string; heartbeat_age_s: number | null;
  installed_version?: string; online: boolean;
}
export interface Options { [k: string]: unknown }

/** One functionality's sizing model, from /api/sizing-models. `measured`
 *  false means no per-pod figure exists for the unit, so there is nothing to
 *  offer or default. */
export interface SizingModel {
  /** The funcId, joining to `Functionality.id` and a location's `func_ids`. */
  functionality: string;
  /** BlazeMeter's own display name, joined on in core. */
  label: string;
  /** What the target counts: "virtual users", "browser instances". */
  unit: string;
  /** What one pod carries, in that unit: "virtual users per engine". */
  figure_unit: string;
  /** What the plan calls this model's pods, e.g. "engines", "mock pods". */
  pods: string;
  measured: boolean;
  /** A starting target for the default saved sizing; served, never invented here. */
  example_target: number;
}

/** What BlazeMeter requires of `slots` before it will create a location with
 *  this funcId, from /api/slot-minimums. */
export interface SlotMinimum {
  /** BlazeMeter's display name for the functionality, as its own error uses it. */
  label: string;
  /** The smallest `slots` accepted. Stated, never applied for anybody. */
  minimum: number;
  /** BlazeMeter's refusal, verbatim. */
  message: string;
}

/** Where the value for one marked field comes from (generate's
 *  PLACEHOLDER_SOURCE). No severity: every marker must be filled before applying. */
export interface PlaceholderSource {
  /** `<NAMESPACE>`: the same string `placeholder.marker` builds. */
  marker: string;
  /** One sentence: where somebody gets the value. Prose, not markup. */
  source: string;
}

/** What one pod of an engine size is rated for, from /api/engine-vus. `rated`
 *  is per model, null where the model has no measured figure. */
interface EngineRating {
  cpu: string;
  memory: string;
  /** The performance figure, in the terms `threadsPerEngine` uses. */
  supported_vus: number;
  rated: Record<string, number | null>;
}

/** One model's answer inside a plan: its target, what a pod carries, and how
 *  many pods that is. */
interface PlanSizing {
  functionality: string;
  unit: string;
  target: number;
  /** Null where no figure has been measured, and then `pods` is null too. */
  per_pod: number | null;
  per_pod_unit: string;
  /** "assumed" is a figure chosen from the pod size; "unmeasured" is one
   *  nobody has. */
  per_pod_source: "supplied" | "assumed" | "unmeasured";
  pods: number | null;
  pods_label: string;
}

/** What a load target costs, from core.capacity_plan. The arithmetic stays on
 *  the server so the planner and doctor share one set of constants. */
export interface CapacityPlan {
  /** The performance model's target in virtual users, or null when no load
   *  test was sized. */
  users: number | null;
  /** Every model asked for, in the server's own order. */
  sizings: PlanSizing[];
  /** The funcId the pod count came from: where several were sized, the largest. */
  driven_by: string;
  vus_per_engine: number;
  vus_per_engine_assumed: boolean;
  engines: number;
  /** `slots` is engines per agent, so concurrency is agents x slots. */
  agents: number;
  engines_per_agent: number;
  engines_per_node: number;
  nodes_per_agent: number;
  nodes: number;
  engine: {
    cpu: string; memory: string; disk_gb: number; tmp_gb: number;
    supported_vus: number;
  };
  node: { cpu: string; memory: string; disk_gb: number };
  peak: { cpu: string; memory: string; disk_gb: number };
  crane: { cpu_limit: string; memory_limit: string };
  /** The four location settings, in the names and units the settings route
   *  takes. `override_cpu` is null when the engine is not whole cores. */
  location: LocationSettings & {
    // Only override_cpu can be missing.
    slots: number; threads_per_engine: number; override_memory: number;
  };
  egress: string[];
  warnings: string[];
  document: string;
  document_file: string;
}

/** One private location's share of an account's rated capacity. */
export interface CapLocation {
  id: string;
  name: string;
  func_ids: string[];
  agents: number;
  /** Agents the payload says are reporting, and agents it says nothing about. */
  agents_reporting: number;
  agents_unknown: number;
  /** Engines per agent. Null when never set. */
  slots: number | null;
  threads_per_engine: number | null;
  engines: number;
  /** Null rather than 0 when slots or threadsPerEngine is unset: unrated, not empty. */
  rated_vus: number | null;
  workspace_ids: number[];
  workspace_names: string[];
  /** In more than one workspace, so its capacity is claimable from either. */
  shared: boolean;
}

export interface Capacity {
  account_id: number;
  workspaces: { id: number; name: string }[];
  locations: CapLocation[];
  /** Shared locations counted once, so not the sum of the workspace totals. */
  rated_vus: number;
  unrated: number;
}

/** How a bundle's AUTH_TOKEN arrived (core.resolve_auth_token): given in the
 *  form, rotated (the previous one is dead), reused from the folder saved to,
 *  or placeholder. Closed on purpose, and held equal to core's by test_server.py. */
export type TokenBranch = "given" | "rotated" | "reused" | "placeholder";

/** What happened to the credential, on every answer that generates a bundle.
 *  `message` is core's own sentence and never contains the token. */
export interface TokenReport {
  branch: TokenBranch;
  /** The ship the token belongs to, where that is known. */
  ship_id: string | null;
  message: string;
}

/** The credential part of a bundle request, spread into the body. Produced
 *  only by token.downloadPlan; `rotate_token` revokes a running agent's token,
 *  so there is no default. */
export interface TokenRequest { rotate_token: boolean }

/** A refusal from this API, with its status. 404 means the thing asked about is
 *  gone (see stale.ts); anything else may come right on its own. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  return (await send(method, url, body)).json();
}

/** The round trip and its failures, for a caller that reads the body itself. */
async function send(method: string, url: string, body?: unknown): Promise<Response> {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail: string | null = null;
    try { detail = (await r.json()).detail ?? null; } catch { /* not our JSON */ }
    // 404/405 with no `detail` is the static mount answering, not this API: the
    // page is newer than the server process serving it.
    if (detail === null && (r.status === 404 || r.status === 405)) {
      throw new Error(
        `this page is newer than the server it is talking to — ${method} ${url} `
        + `is not a route it knows (HTTP ${r.status}). Restart it: `
        + `launchctl kickstart -k gui/$UID/com.blazemeter.bzm-opl-gen.ui, `
        + `or stop and re-run \`bzm-opl-gen ui\``);
    }
    // A 404 with a detail is a route answering about what was asked for.
    throw new ApiError(detail ?? r.statusText, r.status);
  }
  return r;
}

export const api = {
  keyDetect: () =>
    req<{ candidates: KeyCandidate[]; active_key_id: string | null }>("GET", "/api/key/detect"),
  /** The connection the server still holds; a page refresh never drops it. */
  keyStatus: () =>
    req<{ connected: boolean; user?: { email: string };
          default_account_id?: number | null; key_id?: string }>(
      "GET", "/api/key"),
  /** Forget the key the server holds. A key saved to disk stays there. */
  keyClear: () => req<{ connected: boolean }>("DELETE", "/api/key"),
  keySet: (body: { path?: string; id?: string; secret?: string; save?: boolean }) =>
    req<{ user: { email: string }; default_account_id: number | null; key_id: string }>(
      "POST", "/api/key", body),
  accounts: () => req<Account[]>("GET", "/api/accounts"),
  workspaces: (accountId: number) =>
    req<Workspace[]>("GET", `/api/workspaces?account_id=${accountId}`),
  locations: (workspaceId: number) =>
    req<Location[]>("GET", `/api/locations?workspace_id=${workspaceId}`),
  /** Drop the server's cache of BlazeMeter reads, so the next read is real. */
  refresh: () => req<null>("POST", "/api/refresh"),
  createLocation: (body: {
    name: string; account_id: number; workspace_id: number;
    func_ids: string[]; slots: number; threads_per_engine: number;
  }) => req<Location>("POST", "/api/locations", body),
  /** Create an agent and take the credential it comes with, the one moment a
   *  token is free to issue. `auth_token` is null with `token_error` set where
   *  the account refuses tokens; the agent exists either way. */
  createShip: (harborId: string, name: string) =>
    req<{ ship: Ship; auth_token: string | null; token_error: string | null }>(
      "POST", "/api/ships", { harbor_id: harborId, name }),
  /** Issue a new AUTH_TOKEN for an existing agent, revoking the one it runs on. */
  issueToken: (harborId: string, shipId: string) =>
    req<{ auth_token: string }>(
      "POST", "/api/ships/token", { harbor_id: harborId, ship_id: shipId }),
  /** The AUTH_TOKEN this server minted for an agent, if it still holds it. Null
   *  is "holds none"; a failed request is a rejection, never a null. */
  mintedToken: (shipId: string) =>
    req<{ auth_token: string | null }>(
      "GET", `/api/ships/minted-token?ship_id=${encodeURIComponent(shipId)}`),
  /** Drop the remembered token after a hand-typed one replaced it. The ship id
   *  is sent, never the token. */
  forgetMintedToken: (shipId: string) =>
    req<{ forgotten: boolean }>(
      "DELETE", `/api/ships/minted-token?ship_id=${encodeURIComponent(shipId)}`),
  /** Change a location's concurrency settings. Send only the fields being
   *  changed; a blank string means leave it alone. */
  updateLocation: (body: { harbor_id: string }
    & Partial<Record<keyof LocationSettings, string>>) =>
    req<LocationUpdate>("POST", "/api/locations/settings", body),
  facts: (harborId: string) => req<Facts>("GET", `/api/facts?harbor_id=${harborId}`),
  /** Facts from typed values, with no API key. Nothing is validated. */
  manualFacts: (body: { harbor_id: string; ship_id: string; func_ids: string[] }) =>
    req<ManualFactsOut>("POST", "/api/facts/manual", body),
  status: (harborId: string, shipId: string) =>
    req<AgentStatus>("GET", `/api/status?harbor_id=${harborId}&ship_id=${shipId}`),
  /** The live preview. `rotate_token` is false: looking at manifests must not
   *  touch the account. `out_dir` is sent as null because the request model
   *  still declares it. */
  generate: (facts: Facts, options: Options) =>
    req<{ files: GeneratedFile[]; token: TokenReport }>("POST", "/api/generate",
      { facts, options, rotate_token: false, out_dir: null }),
  /** Size a load target; needs no account. Blanks are sent as typed ("" is not given). */
  plan: (body: {
    users?: string; vus_per_engine?: string; engine_cpu?: string;
    engine_mem?: string; engines_per_node?: string; agents?: string;
    /** One row per functionality being sized, in that model's unit. */
    sizings?: { functionality: string; target: string; figure?: string }[];
  }) => req<CapacityPlan>("POST", "/api/plan", body),
  /** What each functionality is sized in (plan.SIZING_MODELS, with labels). */
  sizingModels: () => req<SizingModel[]>("GET", "/api/sizing-models"),
  /** What a pod of this size is rated for, per model. */
  engineVus: (cpu: string, mem: string) =>
    req<EngineRating>(
      "GET", `/api/engine-vus?cpu=${encodeURIComponent(cpu)}&mem=${encodeURIComponent(mem)}`),
  /** Rated capacity by workspace (core.account_capacity). */
  capacity: (accountId: number) =>
    req<Capacity>("GET", `/api/capacity?account_id=${accountId}`),
  optionDefaults: () => req<Options>("GET", "/api/option-defaults"),
  /** The funcId vocabulary: the covered baseline without `accountId`, the
   *  account's own list with one. The answer says which. */
  funcIdVocabulary: (accountId?: number) => req<FuncIdVocabulary>(
    "GET", accountId ? `/api/func-ids?account_id=${accountId}` : "/api/func-ids"),
  functionalities: () => req<Functionality[]>("GET", "/api/functionalities"),
  svConstants: () => req<SvConstants>("GET", "/api/sv-constants"),
  /** {format: {option: why}} for what each format drops (IGNORED_BY_FORMAT).
   *  `{}` is a format that drops nothing, unlike no answer; see formats.ignoredFor. */
  ignoredOptions: () => req<Record<string, Record<string, string>>>(
    "GET", "/api/ignored-options"),
  /** What the server is serving, and whether the page matches its sources;
   *  build.buildNotice words the answer. */
  build: () => req<BuildState>("GET", "/api/build"),
  /** {funcId: slot minimum} (core.SLOT_MINIMUMS). Empty means not read yet and
   *  refuses nothing. */
  slotMinimums: () => req<Record<string, SlotMinimum>>(
    "GET", "/api/slot-minimums"),
  /** {NAME: owning option | null} for the variables a bundle writes itself
   *  (RESERVED_ENV), which `extra_env` refuses. */
  reservedEnv: () => req<Record<string, string | null>>("GET", "/api/reserved-env"),
  /** {option: {marker, source}} for every field that can carry a marker
   *  (PLACEHOLDER_SOURCE). A missing key is a source not read yet. */
  placeholders: () => req<Record<string, PlaceholderSource>>(
    "GET", "/api/placeholders"),
  /** The variables `extra_env` can usefully carry: BlazeMeter's reference minus
   *  what the options write. `funcIds` scopes it server-side; null asks for the
   *  whole reference, while `[]` is a location running nothing covered. */
  agentEnv: (funcIds?: string[] | null) => req<AgentEnvVar[]>(
    "GET", funcIds == null
      ? "/api/agent-env"
      : "/api/agent-env?" + new URLSearchParams({ func_ids: funcIds.join(",") })),
  svMocks: (namespace: string, subdomain: string) =>
    req<SvMocksOut>("GET", "/api/sv-mocks?" + new URLSearchParams(
      subdomain ? { namespace, sv_subdomain: subdomain } : { namespace })),
  svCheck: (host: string, scheme: SvScheme) =>
    req<SvCheckOut>("GET", "/api/sv-check?" + new URLSearchParams({ host, scheme })),
  /** Download the bundle and report what that did to the credential.
   *  `credential` comes from token.downloadPlan and is never defaulted. */
  downloadZip: async (
    facts: Facts, options: Options, credential: TokenRequest,
  ): Promise<TokenReport> => {
    const r = await send("POST", "/api/generate/zip",
                         { facts, options, ...credential });
    // Read before the bytes: the report travels in headers beside the zip.
    const token = tokenFromHeaders(r);
    // The server's name, which is also the folder it extracts to; the local
    // guess is only a fallback.
    saveBlob(await r.blob(), zipNameFromHeaders(r)
      ?? `bzm-opl-${(options.namespace as string) || "blazemeter"}.zip`);
    return token;
  },
};

/** Every route the page calls, handed to App as a prop so a test can pass a
 *  fake (main.tsx picks the real one). */
export type Api = typeof api;

/** Served, because service_virt.py owns the list and new backends must reach the
 *  picker without an edit here. */
export interface SvBackend {
  group: string;
  resources: string[];
  /** What crane publishes with it, e.g. "Ingress", "Gateway + VirtualService". */
  creates: string;
  /** Whether it works with service_type NODEPORT; generate() refuses it where not. */
  nodeport_ok: boolean;
}
export type SvConstants = {
  func_ids: string[];
  /** The backends only; declining is optionGroups.SV_NONE, not listed here. */
  ingress_types: string[];
  backends: Record<string, SvBackend>;
};

/** One funcId in the vocabulary. `label` is BlazeMeter's display name; `covered`
 *  says this tool configures it; `changes_images` is false where it needs the
 *  same images as another; `sub_func_ids` are its parameters (browser pins),
 *  always present, `[]` where there are none. */
export type FuncIdChoice = {
  id: string; label: string; changes_images: boolean; covered: boolean;
  sub_func_ids: string[];
};

/** The vocabulary and which list it is. Against the account, a funcId missing
 *  from `choices` is retired; against the baseline, missing means nothing. */
export type FuncIdVocabulary = {
  source: "account" | "baseline";
  choices: FuncIdChoice[];
};

/** How reading the namespace ended. Cluster access is optional, so an
 *  unreadable cluster is an ok response carrying which reason it was. */
type SvReadStatus = "ok" | "no_cli" | "no_context" | "denied" | "no_mocks";

/** One functionality the configure step can be pointed at, from
 *  /api/functionalities. Option groups tag themselves with `id`. */
export interface Functionality {
  /** The funcId, so a location's `func_ids` join to these by equality. */
  id: string;
  /** BlazeMeter's own display name. */
  label: string;
  hint?: string;
  /** Suggested only while the namespace field still holds a suggestion. */
  namespace: string;
  /** Does its agent run a taurus engine? What makes "engine size" true of its
   *  pods, and what service virtualization is declared apart from. */
  runs_engine: boolean;
}

/** One agent variable the environment area offers (/api/agent-env). An unknown
 *  `type` falls back to a text box; `default` is the agent's own. */
export interface AgentEnvVar {
  name: string;
  type: string;
  /** Which of BlazeMeter's tables document it: "kubernetes", "docker" or both. */
  platforms: string[];
  /** The funcIds whose agent reads it, empty meaning every location. Filtered
   *  on the server; here to be read. */
  functionalities: string[];
  summary: string;
  default: string | null;
  example: string | null;
}

/** A deployed virtual service and the host it answers at, null until a
 *  wildcard domain is configured. */
interface SvEndpoint { name: string; port: number; host: string | null }

/** What is deployed right now, for the watch panel. */
export interface SvMocksOut {
  status: SvReadStatus;
  mocks: SvEndpoint[];
  message: string;
}
/** How probing a published endpoint ended. "ok" means something answered with
 *  a status line, a 503 included. */
type SvCheckStatus = "ok" | "dns" | "refused" | "tls" | "timeout" | "error";
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
/** Save a Blob to disk under `filename`. */
function saveBlob(blob: Blob, filename: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** Where the token report travels on a zip answer. Must match
 *  server.TOKEN_BRANCH_HEADER / TOKEN_MESSAGE_HEADER (test_server.py pins them). */
const TOKEN_BRANCH_HEADER = "X-Bzm-Token-Branch";
const TOKEN_MESSAGE_HEADER = "X-Bzm-Token-Message";

/** The archive name the server chose, or null without one. */
function zipNameFromHeaders(r: Response): string | null {
  const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") ?? "");
  return m ? m[1] : null;
}

function tokenFromHeaders(r: Response): TokenReport {
  return {
    branch: (r.headers.get(TOKEN_BRANCH_HEADER) ?? "placeholder") as TokenBranch,
    ship_id: null,
    message: r.headers.get(TOKEN_MESSAGE_HEADER) ?? "",
  };
}


/** Whether the built page was built from the sources beside it: true (stale),
 *  false (current), "unrecorded" (built before the record existed, so not
 *  known) or null (an installed wheel with no sources). build.ts reads it. */
export type Staleness = boolean | "unrecorded" | null;

/** What the server is serving, from /api/build. */
export interface BuildState {
  version: string | null;
  built: number | null;
  stale: Staleness;
  commit: string | null;
}
