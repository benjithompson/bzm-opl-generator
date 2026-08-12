# Changelog

All notable changes to bzm-opl-gen are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

Group each release's notes under **Added**, **Changed**, **Fixed**, **Removed**
or **Security**, and drop any section that would be empty. Write entries for
the person upgrading: what changed for them, not which files moved. Lead with
anything that breaks.

## [Unreleased]

### Added

- **`--format helm` now covers service virtualization.** The chart was refused
  for a bundle configured to publish virtual services, because it carried
  neither the `KUBERNETES_WEB_EXPOSE_*` environment nor the RBAC the chosen
  ingress backend needs -- so a location that serves mocks had to be generated
  as flat manifests, or installed from the upstream chart. It carries both now,
  and the four options arrive as four values:

  ```yaml
  sv:
    ingress: nginx          # nginx | istio | contour | openshift
    subdomain: apps.example.com
    tlsSecret: wildcard-credential
    istioGateway: ""        # istio only
  ```

  The chart renders the same objects the manifests do -- `tests/helm_parity.py`
  runs every backend both ways -- and refuses the same combinations in the same
  words, in its own copy of them, since a chart is also installed by hand with
  no generator in front of it: `istio` and `contour` under
  `serviceType: NODEPORT`, an OpenShift Route on a plain Kubernetes API server,
  and a gateway name no backend but istio reads. Two things it does not create,
  exactly as the manifests do not: the wildcard DNS record, and the TLS Secret
  in the agent's namespace.

  With that, **no output format refuses a virtual service** -- docker gained
  its own way of publishing one earlier -- so the web UI no longer disables a
  segment, no longer states a functionality as impossible in this bundle, and
  no longer replaces a format you picked. Re-generate an existing chart bundle
  to pick the `sv:` block up.

- **A bundle can now be generated before the private location exists.** The
  harbor id and the ship id are optional everywhere they are typed -- the web
  UI's step 1, `facts --manual`, and the MCP server's `opl_facts manual` -- and
  the bundle carries `<HARBOR_ID>` and `<SHIP_ID>` where each id belongs,
  exactly as a blank credential already carried `<AUTH_TOKEN>`. This is the case
  where the manifests are what a customer's platform team has to approve
  *before* anybody creates the location, and until now the web UI refused to go
  past step 1 without both ids while the CLI refused without `--ship-id`.

  Nothing about it is silent. Each empty box on the page shows the marker it
  will become rather than a sample id, the page names the fields beside the
  boxes and again beside the download button, the bundle's README lists them
  with where each value comes from, and `facts --manual` and the MCP tool say so
  in their own output. **A marked bundle cannot be applied**: a marker is not a
  legal Kubernetes label value, so the API server rejects the crane Deployment
  naming `metadata.labels` and the marker (measured with
  `kubectl apply --dry-run=server`), and the docker bundle's script and its
  `compose up` both refuse before anything is created. Fill the ids in, or
  re-generate once BlazeMeter has issued them.

  Two agents in one location and no `--ship-id` is still a refusal, and
  deliberately: that is a question with two answers rather than none, and
  guessing binds the bundle to an agent somebody else may be running.

### Fixed

- **The configure step's rail called a blank namespace an error.** Placement
  showed a red dot and `needs attention` beside a step that blocks nothing: an
  empty namespace or service account carries `<NAMESPACE>` or
  `<SERVICE_ACCOUNT_NAME>` into the bundle, which is the ordinary way to
  generate manifests before anyone has chosen where they go. It is amber now,
  and reads `not filled in`. Red and `needs attention` are kept for what they
  were for — an option group switched on and left unfinished, which the step
  really does want fixed. The marker itself stays under the box it belongs to,
  where each field's hint already names it and somebody can act on it.

- **`livetest` deployed the CA mode it chose rather than the one the bundle was
  generated for.** `--ca-mode` defaulted to `inline`, and `--local-proxy`
  re-renders the CA whatever the bundle carried — so a bundle generated for the
  file mode (what the web UI now writes) was deployed as an inline one and
  reported a pass, having proved a configuration nobody had generated. The
  default is now the bundle's own mode, read out of `profile.json`, and
  `inline` only where the bundle has no mode this rig can build. Passing
  `--ca-mode` still replaces the bundle's mode, which is how another one is
  deliberately tested, and the run now says which mode it is deploying, and
  what it replaced, before it builds anything. The default is resolved inside
  the rig rather than in the command, so `opl_agent livetest` — the MCP
  server's entry point, which names no mode — gets the same run.

- **A run with no `--local-proxy` deployed a bundle whose CA ConfigMap nothing
  would create.** The `file` and `existing` modes both name a ConfigMap
  somebody else builds — a pipeline holding the certificate, a platform team
  holding the trust bundle — and the rig deploys into a namespace it creates
  itself, where neither is there. The crane pod sat at `ContainerCreating` and
  the run spent its whole 12–20 minutes reporting only that the agent never
  came online. It is now refused before the cluster is built and before the
  credential is minted, naming the ConfigMap and the run that would build it.
  A `profile.json` that sets two CA modes at once is refused there too, with or
  without the proxy: the generator takes one, so a run that re-renders would
  raise with the cluster already built.

- **A bundle README's summary table said the AUTH_TOKEN was in the Secret when
  the Secret held a placeholder.** The row read `AUTH_TOKEN | in
  bzm_secret.yaml` whatever the file contained, four lines above a block naming
  `auth_token` as one of the fields left blank — the summary contradicting the
  detail. It now reads ``AUTH_TOKEN | `<AUTH_TOKEN>` in bzm_secret.yaml -- **not
  supplied**`` where nobody supplied one, and still names the file either way.

  The README is now held to the bundle **mechanically**: a test walks every mix
  of the five fields a form can leave empty, across all three formats, and
  requires the fields the README names and the markers the files actually carry
  to be the same set. A marker the README does not name is a bundle that looks
  finished; a field it names that no file carries teaches the reader to ignore
  the block.

- **A chart bundle's README counted the fields left blank and left the
  AUTH_TOKEN out of the count.** `authToken` is deliberately empty rather than
  marked in a chart — supplying it at `helm install` time is what the bundle
  asks for — so it appears in no row of the "This bundle is not finished" table.
  A bundle with four markers therefore said `4 fields were left blank` over a
  file that needs five values before it can install, and the only mention of the
  token was the install command further down. That block now names it whenever
  nobody supplied one. A chart with every field filled and only the token left
  for install time still gets no banner, which is the state the exemption exists
  for.

- **The web UI would not download a bundle whose service account name was
  empty, having just said it would carry a marker for it.** The configure step
  printed `namespace (<NAMESPACE>) and service_account_name
  (<SERVICE_ACCOUNT_NAME>) are empty, so the bundle will carry those markers
  instead` and the download button then stayed disabled, with nothing on the
  download step saying why. The button's own gate still required a non-empty
  name, from when an empty one refused to generate; a blank required field has
  been its own marker since 0.4.0, so the bundle renders and the gate was
  refusing a bundle the page had already described. Blank fields no longer block
  either button.

  The two fields also lost their red asterisk and their red border. Both
  promised a refusal that no longer happens; a blank one is amber now, and each
  field says which marker it becomes and that the bundle cannot be applied while
  it does.

- **The bundle README and `doctor` described engines and virtual users at a
  location that runs neither.** A GUI Functional bundle's handover said
  `2 engine(s) per agent at 500 virtual users each` and `doctor` reported
  `500 threads on a 1 CPU / 4Gi engine`, over a location that BlazeMeter sizes
  in browser instances -- and that is the sentence somebody reads while deciding
  how much cluster to ask for. Both surfaces now speak the location's own model,
  read off its funcIds:
  - A **GUI Functional** bundle states its sizing in browser instances and about
    how many one engine of the configured size carries. The `threadsPerEngine`
    figure is still printed as what the account stores, and is **not** relabelled
    as browser instances -- an engine that size carries about four of those, so
    pouring one figure into the other unit would be worse than the wording it
    replaced.
  - A **service-virtualization** bundle describes no engine at all: its agent
    carries none. A cluster bundle states the limits pair in the word for the pod
    that gets it; a **docker** bundle carries no limits pair (they are Kubernetes
    variables and its README already said so), so it states no per-pod size
    rather than a mechanism it does not have. Either way the bundle says plainly
    that requests per second per mock pod has not been measured, and the docker
    socket and host-sizing bullets stop naming engines.
  - A location whose funcIds this tool has no model for (`tdm`, `dataPublisher`,
    `delphix`) gets its two figures stated without a unit rather than read as
    performance. `doctor` keeps the performance ratio there -- it is the only one
    there is -- and now says whose ratio it is, and separately says when the
    funcIds were never read at all.
  - `doctor`'s engine-heap check no longer warns about an unset JVM heap on an
    agent that runs no JVM.

  Every performance bundle is unchanged, byte for byte, and every verdict on a
  performance location keeps its wording.

- **The bundle named a namespace it never created, so the README's first
  command failed.** Every object in a Kubernetes bundle carries the namespace,
  no file in the bundle is the namespace, and the README said nothing about it —
  so following the Deploy block on a fresh cluster stopped at
  `Error from server (NotFound): namespaces "blazemeter-perf" not found`. The
  Deploy block now leads with the command that creates it:

  ```
  kubectl get namespace blazemeter-perf >/dev/null 2>&1 || kubectl create namespace blazemeter-perf
  ```

  It asks first, so a namespace that already exists is left exactly as it is and
  the block is safe to follow either way. **Do not replace it with the shorter
  `create --dry-run=client -o yaml | apply -f -`**: that form was measured
  deleting the labels off a namespace somebody else had created with `apply`,
  `pod-security.kubernetes.io/enforce` among them. The bundle still carries
  **no** Namespace manifest, deliberately: a manifest that owned the namespace
  would make a later `kubectl delete -f .` take the namespace and everything
  else inside it, including whatever else the customer runs there. An OpenShift
  bundle prints the same line with `oc`, and a bundle that has to create a
  trust-bundle ConfigMap first prints it above that step as well. A `--format
  helm` bundle already created the namespace with `--create-namespace` on its
  install line and is unchanged; a `--format docker` bundle has no namespace and
  still mentions none.

- **`livetest --local-registry` said nothing about the one image it never
  pulls.** The flag mirrors the location's images into a local registry and
  blackholes the public ones, so a bundle that names an image the registry does
  not hold fails the run. That holds for every image the agent pulls, and not
  for the engine: no engine exists unless `--run-test` starts one, so a
  crane-only run pulls no engine image and a wrong engine reference passes. A
  private-registry defect survived months of green runs that way. The run now
  prints a warning naming that gap, and continues -- what the flag does cover
  without a test is crane's own image, plus the blackholed public registries on
  minikube, which is the only cluster the blackhole acts on. Pass `--run-test`
  as well and the run says nothing, having covered the engine. A location that
  runs no engine -- a service-virtualization agent carries none -- gets no
  warning either, having no engine image to miss.

## [0.4.1] — 2026-08-08

### Added

- **A CA slot now refuses to start.** A `--format manifests` bundle generated
  with `--ca-placeholder` used to apply cleanly and run a `1/1 Running` agent
  that could never come online -- the marker is a ConfigMap value, so the API
  server takes it, and the only evidence was `NO_CERTIFICATE_OR_CRL_FOUND` in
  crane's own log. The bundle now carries a `ca-slot-check` initContainer, so
  `kubectl get pods` shows `Init:Error` (then `Init:CrashLoopBackOff`) and
  `kubectl logs -c ca-slot-check` names the field, the file and the fix. It runs
  crane's own image, which is already mirrored and pull-secreted, so a sealed
  cluster with the public registries blackholed pulls nothing new for it. Helm
  and docker keep the refusals they already had; nothing is duplicated into
  them.
- **`generate` says so when it writes one.** One line beside the `AUTH_TOKEN`
  line, naming the file to fill in for that format and what stops each one. The
  CLI prints it and the MCP server carries it as a warning.

### Fixed

- **The waiting-for-a-certificate block named a file two formats do not have.**
  It said `bzm_cacerts.yaml` whatever the format, and a chart bundle carries no
  such file while a docker bundle carries no ConfigMap at all. Each format now
  names the file that is in its own directory.

Every bundle generated without `ca_bundle_slot` is unchanged, byte for byte.

## [0.4.0] — 2026-08-08

### Added

- **`--format docker` can serve virtual services.** It used to refuse a bundle
  configured for service virtualization, and the refusal was always narrower
  than it read: a docker agent publishes them perfectly well, and the gap was in
  this generator. Three new options are that shape — `--sv-hostname` writes
  `HOSTNAME_OVERRIDE`, and `--sv-tls-cert` / `--sv-tls-key` are written into the
  bundle as `sv-tls.crt` and `sv-tls.key`, mounted at BlazeMeter's own paths and
  named by `TLS_CERT` and `TLS_KEY`. They take a PEM *file* and carry its
  contents, exactly as `--ca-bundle` does, and both mounts stay overridable at
  run time (`SV_TLS_CERT`, `SV_TLS_KEY`). The compose file mounts the same pair.

  Two things are checked when the bundle is generated, because both fail
  silently on a running agent: the key must be **PKCS#8** (a PKCS#1 export is
  refused, naming `openssl pkcs8 -topk8`), and `--sv-hostname` must match a
  DNSName in the certificate's SAN or its Common Name, wildcards included.
  Nothing else about the certificate is checked — not expiry, not the chain, not
  whether the key beside it is its key — and where the certificate cannot be
  parsed at all the bundle's README says the hostname was **not checked**,
  rather than going quiet.

  The four Kubernetes `sv_*` options are now *ignored* by `--format docker`
  rather than refused, and the three above are ignored by the two cluster
  formats: each set is the other platform's, so a profile written for one and
  generated for the other keeps its values and the README names what it could
  not apply. **`--format helm` is now the only format that refuses a virtual
  service.** `sv_tls_key` is a credential, so it is not written to
  `profile.json`: `generate --profile` on such a bundle needs `--auth-token`
  *and* `--sv-tls-key`. See
  [docs/service-virtualization.md](docs/service-virtualization.md).

- **`--format docker` now emits `compose.yaml` beside `bzm-opl-agent.sh`.** Some
  customers install with Docker Compose and will not take a `docker run` script.
  It is not a fourth `--format`: a format is a platform, and these are two
  syntaxes for one — `docker compose up -d` in the unzipped directory starts the
  same container the script does, from the same credential file
  (`bzm-opl-agent.env`, read as `env_file:`). They are **either/or**, and docker
  enforces it rather than the README: both name the container
  `bzm-crane-<shipId>`, so whichever you run second refuses with the name in the
  message. Running both would put two cranes on one agent identity, which
  BlazeMeter reports as duplicated results rather than as an error.

  The bundle never contains a file called `.env`, and the compose file says why:
  compose auto-loads that one for variable interpolation into the compose file
  rather than into the container, so a token moved there would silently never
  reach the agent. For the same reason every inline value is written with `$`
  doubled.

### Changed

- **A field left blank now carries a marker that names it.** Every required
  field left empty used to resolve to one shared `<PLACEHOLDER>`; it now
  resolves to `<KEY>`, the option's own key in upper case, with a dotted key
  joined by an underscore. So a bundle carries `<AUTH_TOKEN>` in its Secret,
  `<NAMESPACE>` in every object's metadata, `<CA_BUNDLE>` where a CA slot waits
  for a PEM, `<SV_TLS_KEY>` inside `sv-tls.key` and `<PROXY_HTTPS>` inside a
  proxy URL. Somebody handed an unfinished bundle can now read which field is
  missing out of the file itself. Before, the answer was only in the README
  table, and the person who applies a bundle is routinely not the person who
  filled the form in.

  Every message that quotes a marker quotes the one it is about: the README's
  "not finished" block gained a **marker** column beside each field, the
  generated `bzm-opl-agent.sh` and `compose.yaml` name the marker in their
  refusals, `helm install` prints the value it found, and `bzm-opl-gen livetest`
  names each blank field beside its marker. The web UI's warning pairs each
  field with its own marker too.

  **Nothing about the guard changed.** `<AUTH_TOKEN>` is no more a legal
  Kubernetes name than `<PLACEHOLDER>` was, so `kubectl apply` goes on rejecting
  a marked `namespace` or service-account name and naming the field, and goes on
  accepting a marked *value* — which is why the README still prints a `grep`.
  That `grep` now matches any marker rather than one string.

  Two smaller effects. A bundle generated by an earlier version is still
  recognised: every reader matches the shape `<KEY>`, so a `profile.json` or a
  Secret holding `<PLACEHOLDER>` is still read as a blank field rather than as a
  value somebody meant. And the CA slot's suggested command is now quoted —
  `grep -c <CA_BUNDLE> file` is a shell redirect from a file called `CA_BUNDLE`,
  so the command as printed failed with "no such file" and said nothing about
  the bundle.

### Fixed

- **The stale-page banner no longer fires after a `git pull` that changed
  nothing.** `bzm-opl-gen ui` decided whether its built page matched the code
  serving it by comparing timestamps — the newest file under `frontend/src`
  against the built page's. `git pull`, `git checkout` and a branch switch all
  rewrite the files they touch, so a fast-forward through two merged pull
  requests raised the banner with the built output byte-identical. It now
  compares content: a build records a fingerprint of its sources beside the
  page, and `/api/build` compares that with the sources on disk.

  `/api/build`'s `stale` gained a fourth value with it, and each of the four is
  worded as itself. `true` is a page not built from these sources and is the
  only one shown as a warning; `false` is compared and current; `"unrecorded"`
  is a page built before the fingerprint existed, which says so plainly and
  names the rebuild that answers it — it is **not checked**, not a warning, and
  not the wheel's answer; `null` stays the installed wheel, which has no
  sources for the question to be about. A page and a startup line that cry wolf
  after every pull are ones people learn to ignore, which is exactly what the
  failure they exist for cannot afford.

- **A Kubernetes bundle now mirrors every image to the reference crane actually
  pulls.** With `--private-registry`, `IMAGE_OVERRIDES` mapped
  `taurus-cloud:<tag>` to `<registry>/v4:<tag>` and the mirror script pushed
  exactly that — while crane created the engine pod asking for
  `<registry>/blazemeter/v4:<tag>`. So an air-gapped cluster took the whole
  bundle, brought the agent online, and failed the **first test** with
  `ImagePullBackOff: manifest unknown`. Measured live on 2026-08-08; pushing the
  images a second time under the composed shape made the same run pass.

  The destination now keeps the image's whole repository path below BlazeMeter's
  public registry — `<registry>/blazemeter/v4:<tag>`,
  `<registry>/blazemeter/charmander/<browser>:<tag>` — for `--format manifests`
  and `--format helm` alike, and the `IMAGE_OVERRIDES` value and the mirror
  script's push target come from one function, so the two cannot name different
  references. **Re-generate and re-run `bzm-opl-image-mirror.sh` for any
  existing private-registry bundle**: the images already in your registry stay
  where they are, and the run adds them under the path crane asks for.

  Crane's own image is unchanged and still `<registry>/crane:<version>` — the
  bundle names that reference itself, in the Deployment and in the chart's
  `image.repository`, which is why crane pulled correctly throughout. Two other
  copies of the destination rule followed the same correction:
  `bzm-opl-gen images --mirror` (and the MCP tool behind it) and the live rig's
  own `--local-registry` mirror.

  **Only the engine reference was observed live.** The location's other images
  were pushed under both shapes rather than tested under one, so which reference
  crane asks for them by is inferred from the engine and not measured. It is
  also unknown whether crane ignores `IMAGE_OVERRIDES` for engines or looks the
  entry up under a key the map does not carry; the fix does not depend on which
  is true, because the map's value is now the composed name either way.

- **A `--format docker` bundle for a location that serves virtual services now
  names the images to pre-pull.** Crane does not pull on a Docker agent: it
  composes the image name and asks the daemon to *create* the container, so an
  image the host does not already hold makes the first virtual service retry for
  about ninety seconds and end `FAILED` — `Failed to find a deployed container`
  in BlazeMeter and `No such image` only in `docker logs`, neither of which
  mentions a pull. The README's **Worth knowing** section now carries the exact
  `docker pull` commands, and says what to do on a host that cannot reach the
  registry.

  The tags are the **`:latest`** forms under the configured `DOCKER_REGISTRY`,
  not the versions the location pins: crane prefixes that registry onto
  BlazeMeter's own unqualified image name, which carries `latest` whatever
  `/versions` says. Only the mock images are named — that is what a live run
  measured — so a performance-only docker bundle says nothing about pre-pulling.

- **...and `--private-registry` now mirrors those images to the name crane
  actually asks for.** A `--format docker` bundle for a virtual-service location
  used to push `<registry>/service-mock:<pinned version>` while crane went on to
  create `<registry>/blazemeter/service-mock:latest` — both the path and the tag
  differed, so the mirror ran, reported success, and the first virtual service
  failed on a missing image anyway. The mirror script's push list and the
  README's `docker pull` list are now one set.

  This shape is the docker one, composed from the crane key; the Kubernetes
  formats compose from the repo path instead, and the entry below is where that
  half was put right. Crane's own image keeps the short
  name on docker too — the bundle's `bzm-opl-agent.sh` and `compose.yaml` name
  that reference themselves. The script now says which of its two destination
  shapes is which, for whoever is reading it beside the README.

- **A docker bundle no longer sets `DOCKER_REGISTRY` unless you mirror**, which
  is what BlazeMeter's own generated command does. It was always written,
  defaulting to BlazeMeter's public gcr mirror, and that made a default
  performance bundle unable to run a test at all: crane composes
  `<DOCKER_REGISTRY>/<key>:latest` and the keys are not uniform — the mock ones
  carry the org, the engine one does not — so the engine resolved to
  `gcr.io/verdant-bulwark-278/taurus-cloud:latest`, a path with **zero tags**.
  Crane does not pull, so the run sat at `BOOT_STARTING` with no engine
  container and `No such image` only in `docker logs`.

- **...and where you do mirror, the mirror and the README's pre-pull list now
  cover the engine images too.** Both were scoped to virtual services while the
  engine half was unmeasured; a live docker performance agent has since asked
  for `<registry>/taurus-cloud:latest`, the same rule with no org because the
  key has none. A performance docker bundle now carries the pre-pull bullet
  rather than saying nothing about the trap most likely to hit it. **With no
  registry configured the bullet names the image keys and no commands** — the
  prefix is then crane's own default, which nothing here has measured.

- **A docker bundle no longer mirrors the crane-hook image**, which that same
  bundle's README lists under "Set here, but not carried" — the hook is a Pod
  and there is no cluster to run it in. The mirror script read `crane_hook`
  directly rather than through `ignored_options`, so one bundle said both
  things, and a customer's registry took a push of an image that format can
  never pull. Kubernetes and Helm bundles still mirror it, since that is where
  it runs.

  The rule now has a test that walks every format's ignored table and requires
  the emitted files to be byte-identical whether the option is set or not —
  `README.md` and `profile.json` excepted, both of which name it by design. The
  refusal sweep beside it could not have caught this: nothing was refused,
  something was quietly carried.

## [0.3.2] — 2026-08-06

### Added

- **Open source: Apache-2.0, and on PyPI.** `pipx install "bzm-opl-gen[ui]"` is
  the install — no `gh auth`, no git URL, no access to ask for. The git spec and
  the release wheel still work, for `main` or a tag PyPI has not seen. Releases
  publish through trusted publishing, so there is no API token to leak.
- **A LICENSE, a NOTICE, and the metadata a package needs to describe itself** —
  the wheel had no license, README or URLs, so its PyPI page would have rendered
  blank. Plus issue and PR templates, CODEOWNERS, and Dependabot over all three
  ecosystems this repo builds from.
- **The planner sizes all three covered functionalities, each in its own unit.**
  It only spoke virtual users, and two of the three are not asked for in those.
  `plan` now takes `--browsers` and `--requests-per-second` beside `--users`,
  any of them alone or together; `--users` is no longer required, though a run
  naming none of the three is still refused.

  | functionality | asked for in | one 2 CPU / 8Gi pod carries |
  |---|---|---|
  | Performance | virtual users | 500 — BlazeMeter's own figure |
  | GUI Functional | browser instances | about 4 — the account owner's estimate |
  | Service Virtualization | requests per second | **nothing measured** |

  **One pod size across all of them**, because the agent applies a single CPU and
  memory limit pair to every pod it creates. Where several are sized the largest
  decides, and the plan names which — largest, not the sum, so if a load test and
  a browser suite run together, add the pod counts yourself. **Service
  virtualization is stated, not sized**: requests per second per core has never
  been measured, nothing is assumed in its place, and there is no flag to supply
  one. Sized alone it is a refusal carrying that sentence, not a plan with an
  invented number in it.
- **Named sizings on the web UI's step 1.** Save what is in the fields under a
  name and pick it back later; three ship as starting points, one per
  functionality. Picking one fills the fields and applies nothing else.

### Changed

- **The CLI now has one dependency, `cryptography`.** It had none, and this is
  a deliberate departure: checking `--sv-hostname` against the certificate above
  needs an X509 parser, and the standard library has no public API for reading a
  certificate that did not arrive over a live connection. `pipx install
  bzm-opl-gen` pulls it in; `bzm_opl_gen/cert.py` is the only module that uses
  it, and no other dependency was added with it.

- **A pull request on a public repo can no longer reach a self-hosted runner.**
  `runs-on` reads the trigger and `github.event.repository.private` as well as
  `RUNNER_LABELS`. Without it a fork PR would run a stranger's `conftest.py`,
  build backend and npm lifecycle scripts on the maintainer's own machine — on
  reused, non-ephemeral runners, the same host that later builds the release
  wheel. Keyed on `private` because a private repo cannot be forked by a
  stranger; it fails safe, since an absent `private` reads as public.
- **A bundle's images come from the location itself, and no longer wait for a
  running agent.** BlazeMeter serves the image list a location runs — the list
  the agent asks for at startup — so `facts` reads it directly. Three things
  follow: **the GUI browser image is named** (the one gap this tool said it could
  not close, since a location runs one of 60-odd version-pinned builds and there
  is no defensible default), **versions are exact** rather than `latest`, and
  **the images follow what the location runs**. Where the list cannot be read, a
  running agent's inventory and the built-in catalogue still fill in behind it —
  and `facts` now says when the list was *refused*, which is not the same as a
  location that names nothing.
- **An MCP session can size all three functionalities, and is told so.**
  `opl_plan capacity` required `users` and described virtual users alone, which
  for a GUI or SV customer was the whole of what could be asked. It now takes
  `browsers` and `requests_per_second` too. `opl_location create` and
  `opl_facts manual` also name the funcIds a bundle can be configured for.
- **The MCP server says agent, not ship.** For a session driving this server the
  tool descriptions are the whole documentation, so "a location with no ship"
  left it guessing. `create_ship` is now **`create_agent`**, listings count
  `agent_count`/`agents_reporting`/`agents_unknown`, and `create_ship` still
  works as an alias. **`ship_id`, `harbor_id`, `SHIP_ID` and `HARBOR_ID` are
  unchanged** — those are BlazeMeter's own field names.
- **Entering an identity by hand declares everything it runs, not one thing.**
  The *Enabled* control was a radio button, so a typed identity could be
  Performance *or* GUI Functional, never both — but 71 of 168 locations in one
  real account run both, and the bundle carried the wrong images for half of
  what they do. It is a checkbox now. Ticking two suggests one namespace, and a
  namespace you typed still wins.
- **Service Virtualization is declared on its own, where you are deciding.** In
  manual entry and the create-location form, ticking it clears the engine
  functionalities and vice versa, with the reason on screen first: one limit pair
  reaches every pod, and an SV agent carries no test engine at all — two sizing
  problems sharing one number. **A location that already runs both still
  generates**; what a location *is* is changed in BlazeMeter, not here.
- **`AUTO_KUBERNETES_UPDATE` is findable.** It was never missing — the bundle
  writes it from *Agent self-update* under **Security & RBAC** — but nothing led
  from the variable's name to that control. The environment area now lists every
  variable the bundle writes for itself, the option that writes it, and the
  section holding it.
- **The funcIds on screen are your account's, with BlazeMeter's own names.** The
  list was hand-written here and disagreed with real accounts in both directions.
  `functionalApi` is no longer offered when creating a location — BlazeMeter
  retired it — though a location carrying it still reads and generates. With no
  key connected the list is the three this tool configures.

### Fixed

- **A GUI Functional private location could not be created at all.** BlazeMeter
  refuses one whose "Parallel engine runs" is 1, and `slots` defaulted to 1
  everywhere, so every one this tool made was a 400. Found on a live POST; the
  constraint is not in BlazeMeter's documentation. The rule is now applied where
  the functionalities are chosen, before the write. **Nobody's `slots` is raised
  for them** — it is engines per *agent* and a real cost.
- **A card on the configure step is one functionality, under BlazeMeter's own
  name.** A performance-only location was given a card labelled "Performance &
  functional testing", because one entry stood for four funcIds. There are three
  cards now, and a location opens on the one it actually runs. A tab left open on
  this step starts over rather than half-reading an older snapshot.
- **A bundle for a location that runs no load tests keeps its pod limits.** They
  were cleared for anything not running Performance, on the reading that they
  size an engine. They do not — one pair reaches *every* pod — so SV and GUI
  bundles were landing on the agent's 250m/256Mi defaults: enough to look healthy
  and not enough to run on.
- **A browser your GUI Functional location is pinned to is not a capability this
  tool is missing.** Pins like `chrome:default` arrive as funcIds beside
  `functionalGui`; 43% of one account's 171 locations carry at least one and the
  worst carries 41, all of which were being listed as unconfigurable. A pin is a
  parameter of GUI Functional, not a capability. What remains says two different
  things properly: a funcId this tool configures nowhere, and one your account
  has retired.
- **The environment list is the variables *this* location's agent reads.** It was
  filtered by platform only, so a performance-only location was offered nine
  variables it has no reader for — the Selenium grid's and the virtual-service
  publishing ones. **Nothing is refused or cleared**: a variable already set stays
  editable and is still written.
- **Changing what a manually entered identity runs no longer hangs the page.**
  Two rules read the same question from different sources — one cleared the
  ingress, the other restored it from facts fetched for the previous declaration.
  They read one source now.
- **The wheel no longer carries stale UI bundles.** setuptools stages into
  `build/lib` and does not clear it, and `ui_dist` filenames carry a content
  hash, so a rebuild never overwrote its predecessor and the wheel shipped both —
  ~300KB of dead weight per install.
- **Placeholder ids in the web UI and tests are obviously fake.** The Harbor ID
  placeholder was a real location's id, compiled into the shipped bundle.

### Removed

- **`SECURITY.md` and `CODE_OF_CONDUCT.md`.** Neither earned its place on a
  single-maintainer project: the code of conduct governed a community that does
  not exist, and the security policy restated the README. Where to report a
  vulnerability is unchanged — a GitHub security advisory, private until
  published — it is just said once now, beside where bugs go.

## [0.3.1] — 2026-08-05

Environment variables the options do not cover, a marker in every field left
blank, and an install command that works.

**0.3.0 was tagged but never published** — its release build died at the last
step on a runner with no `gh`, which is fixed below. Nothing installed it, so if
you are coming from 0.2.0, the 0.3.0 section further down is part of this
upgrade too.

### Added

- **Environment variables the bundle has no setting for.** BlazeMeter's agent
  environment reference is much wider than the options this tool exposes —
  `PREFERRED_INTERFACE`, `KUBERNETES_USE_PRE_PULLING`, `DODUO_PORT` and the
  rest — and the only way to reach the others was to edit the generated
  ConfigMap by hand, which the next `generate` silently overwrote. A new
  `extra_env` option carries them: an *Environment variables* fold on the
  configure step, `--env NAME=VALUE` on the command line, repeatable. All three
  formats carry it — ConfigMap entries for `manifests`, `extraEnv` in the values
  overlay for `helm`, `--env` flags for `docker` — and it is in `profile.json`,
  so a regenerate replays it.

  In the web UI it is a **list, not a blank box**: open the fold and every
  variable BlazeMeter documents that no setting above it already writes is
  there, with the agent's own default beside it, so nothing has to be spelled
  from memory. Each row carries the control its type deserves — a key/value
  table for `KUBERNETES_LABELS` and `KUBERNETES_CUSTOM_ANNOTATIONS_JSON` so
  nobody hand-encodes JSON, a box a certificate fits in for the TLS pair, and
  three positions for a boolean (*Default*, *On*, *Off*), because leaving it
  alone writes nothing and is a different answer from switching it off. Which
  half of the reference is shown follows the format: crane's variables for a
  Kubernetes bundle, the container agent's for a docker one. Under the list,
  *Another variable by name* still takes anything the list does not carry.

  It reaches the **agent**: crane's pod reads it, and the engines crane spawns
  do not, because crane builds their environment from the `KUBERNETES_*`
  variables rather than passing its own down.

  A variable the bundle already writes is **refused**, naming the option that
  sets it, rather than being silently duplicated: two values for one key is a
  ConfigMap with a duplicate entry, and whichever wins is not the one you typed.
  Kubernetes variables are refused in a docker bundle too — they reach nothing
  there either, and accepting one would read as a setting that had been made.

### Changed

- **The cluster check moved to the download step.** *Cluster check
  (crane-hook)* was among the configure step's agent settings; it is now **Ship
  the check with the bundle**, under *Preflight the target cluster* on
  **Download & verify**, beside the other two ways of asking the same question
  (*Test deploy*, and the evidence file). It shapes nothing about the agent —
  the same deployment is applied either way — and what it is about is the
  cluster the bundle is going to. The option, the manifest it emits and the
  Helm `helm test` hook are unchanged; only where you switch it on has moved.

### Fixed

- **The documented install command did not work, and installing is now one
  line.** Every copy of it — the README, `docs/mcp.md`, and the footer appended
  to every GitHub Release's notes — said `pipx install
  './bzm_opl_gen-*.whl[ui]'`. Quoted, that glob is expanded by neither the shell
  nor pipx, so the first command of the first step exited `Unable to parse
  package spec`. Installing is now a single line that downloads nothing first:

  ```
  gh auth login && gh auth setup-git
  pipx install "bzm-opl-gen[ui] @ git+https://github.com/benjithompson/bzm-opl-generator@v0.3.0"
  ```

  `gh auth setup-git` is the new part and the load-bearing one: it leaves the
  token where plain `git` — and so `pip` — will find it, which is what the
  README previously said made a `git+https://` install impossible. The release
  wheel is still attached to every release and still installable; the docs now
  name it by its real filename rather than by a glob. The prebuilt web UI is
  committed, so there is no npm step on either route.

  A new `tests/test_install_docs.py` holds the three copies to the same spec and
  refuses a `*` in a quoted install command, because the release workflow
  checked the wheel's contents thoroughly and the prose not at all.

- **The README says how to run it from a checkout.** It described the release
  wheel and, under *Contributing*, an editable install with the test extras —
  nothing that read as "you have cloned this and want the page".

- **Releases publish again.** The release job ended in `gh release create`, and
  the self-hosted runners this repo moved to do not carry `gh` — so v0.3.0 built
  the wheel, passed the suite, verified the wheel's contents and assembled its
  notes, then died on `gh: command not found` with nothing published. It now
  creates the release and uploads the wheel and sdist through the API, on the
  node the runner already runs every other action with, so there is no binary to
  be missing.

## [0.3.0] — 2026-08-03

The docker output format, a capacity planner that needs no account, and a web UI
that survives a refresh. Nothing in this release breaks a bundle generated by
0.2.0, but `generate --api-key` stopped fetching an AUTH_TOKEN — see **Changed**
below if you script it.

### Fixed

- **An AUTH_TOKEN this app issued survives a refresh.** The page is shown a
  credential at exactly two moments, both its own writes — creating an agent,
  and *Regenerate* — and nothing reads one back afterwards, because BlazeMeter
  shows it once. The browser held the only copy, so a reload lost it for good
  and the next bundle fell back to a placeholder for an agent created a minute
  earlier, leaving you to redo by hand the one piece of work already done for
  you. The server now keeps what it minted, keyed by agent, for as long as it
  runs; a restart forgets, which is the intended lifetime rather than a gap in
  it. A token you *typed* is still never stored — pasting one evicts the
  remembered copy rather than sitting behind it, so what you entered cannot be
  silently replaced on the next load.

- **An imported preflight, and the undo for what it applied, survive a
  refresh.** What a reload used to keep was only the damage: values applied from
  a suggestion are options, and options were remembered, while the verdicts
  explaining them and the history reversing them were not. The bundle came back
  carrying a change the page could no longer account for or take back — and
  re-picking the evidence file is not always possible, since whoever ran the
  collector may not be whoever is at the browser. Verdicts, suggestions, the
  file name and the undo history are now restored together. The evidence
  *document* itself is deliberately not kept: it grows with the cluster while
  the answer does not. A restored answer cannot be re-judged against later
  option changes, so the panel says so, with the file named.

- **A manually declared bundle is still what it was declared to be after a
  refresh.** In manual entry the feature radio is not a view over a location —
  it is the declaration, naming the funcId the typed identity runs, which names
  the images the bundle carries. It was the one bundle-deciding input a reload
  did not restore, so a service virtualization identity came back a performance
  one; the options belonging to the feature it no longer looked like it ran were
  cleared on the way past, and the namespace suggestion rewrote a name generated
  into every manifest. The declaration is now restored with the ids it belongs
  with, and checked against the features the server still offers rather than
  trusted — one no longer offered is dropped, landing the page where a fresh
  manual session lands instead of gathering facts for a funcId nothing names.

### Changed

- **Step 1 asks for the choice to be made, and says how to make it.** Choosing a
  location expanded its settings onto a panel whose only control was *Save*,
  greyed whenever nothing had been typed — which is most locations, since most
  are already configured. The common path opened a form with a dead button and
  nothing pointing at what to do next. There is now one button, always live,
  labelled for what pressing it does: **Confirm** folds the location away and
  opens the agent list, writing nothing; **Save** appears only once something is
  edited, with the sentence beside it saying what that costs. The two are
  deliberately not one control that sometimes writes to your account.

  *Next* now waits for both the location and the agent to be confirmed, rather
  than merely selected. Both lists select on their own — a lone agent is
  auto-picked, and a restored session brings back a pairing nobody has looked at
  this time round — so "something is selected" was never the same question as
  "somebody has said this is the one". Manual entry is unchanged: typing a
  harbor id and a ship id by hand is already the deliberate act.

### Removed

- **The download step drops five things it was doing twice.** **Save to folder**
  was a second way to produce the same bundle, on the machine running the server
  rather than in the browser; writing a directory is `bzm-opl-gen generate -o`
  and the MCP server's `opl_bundle`, and both are where somebody who wants one
  already is. This step hands over a zip. **The rotate box** was a second way to
  mint a credential — the first is on step 1, on the agent the credential
  belongs to, beside the sentence saying what it revokes, which is where the
  question is actually asked; two controls for one irreversible thing is one
  more than a page can keep honest. Three lines went with them: the "nothing was
  issued" note under every download, which taught people not to read that line
  (it is kept for the branch where something *did* happen), the format
  restatement on arrival, and the paragraph under the placeholder warning. The
  headline stays.

  `POST /api/generate/save` is untouched and still tested — it is a served
  capability rather than a control on this page.

### Fixed

- **A generated docker bundle now starts.** The container ran as the crane
  image's own non-root user, and `/var/run/docker.sock` is `root:docker 0660` on
  a stock daemon — so the agent came up, reached the socket and died with
  `PermissionError(13, 'Permission denied')`, a traceback naming neither the uid
  that could not open it nor the flag that would have. `-u 0` is now set, as
  BlazeMeter's own generated command has always set it. Starting engines through
  that socket is the only thing the agent does, so the bundle was unusable
  without it.

  `DOCKER_PORT_RANGE` was missing for the same reason and is now set too:
  `--net=host` makes an engine's ports the host's ports, and their command
  always names the range.

  Both are in the command their API returns and in neither of the pages
  describing it, which is what building this format from the documentation
  alone cost.

### Added

- **`--format docker`: the agent as one container on a docker host.** A third
  output format beside the manifests and the chart, and a different platform
  rather than a third rendering of the same objects — the bundle is a
  `docker run` script, an `.env` file and a README. Two dozen options are
  Kubernetes vocabulary and reach nothing here; each is **named** in the
  bundle's README where it was set away from its default, so a bundle handed
  over cannot be believed to have applied a node selector it dropped. Service
  virtualization is refused with the reason, not silently omitted. See
  [docs/docker.md](docs/docker.md).

- **The web UI asks for the output format first, and the form follows it.** The
  three formats used to be picked on *Download & verify*, one step after a
  Configure page that asked for a namespace, a service account, node selectors
  and engine limits — none of which a docker bundle carries. The choice now
  sits at the top of Configure and everything a container has no such thing as
  goes off screen: placement is its own section and disappears whole, Scheduling
  and Engine sizing go, and the fields inside Private registry, Custom CA trust
  and Security thin out to the ones that reach something. Nothing is discarded —
  a value set for Kubernetes survives the switch, is still in `profile.json`,
  and is still named in the README.

### Removed

- **The web UI no longer offers to turn a feature on for a location, and
  `POST /api/locations/func-id` is gone with it.** Which funcIds a private
  location carries is what the location *is*, and BlazeMeter's own UI (Settings
  → Private Locations) is where it changes; the other two writes this page makes
  change an agent's credential and a location's concurrency, which is what a
  page for configuring a bundle is for. `core.add_func_id` and
  `api.update_private_location`'s `func_ids` went too — with nothing adding them
  additively, the parameter was only the wholesale-replace hazard it existed to
  guard. `bzm-opl-gen create-location --func-ids` is unaffected.

### Fixed

- **A format that cannot serve service virtualization says so, instead of
  offering it.** The Helm and Docker segments were taken away for a location
  whose funcIds *demand* virtual services, but the generator refuses on what is
  *configured* — it never looks at the funcIds first. Between the two sat a
  location carrying only funcIds this tool models no feature for (`tdm`,
  `dataPublisher`, `delphix`): nobody had said whether it runs mocks, so every
  switch was offered, nothing cleared them, both segments stayed enabled, and a
  complete SV configuration generated as a docker bundle the server then refused
  outright — with nothing on screen having said so. The segments now follow the
  configuration, and on a Helm or Docker bundle the **Service virtualization**
  card states that this bundle cannot serve it and offers nothing to press —
  which is a different answer from "not enabled on this location", and reads as
  one. Switching to **Kubernetes manifests** brings the controls back.

- **The output format is never replaced in silence.** Turning service
  virtualization back on with Docker selected, or importing a profile that
  pairs the two, moved the segment to *Kubernetes manifests* without a word.
  The page now says which format was replaced and why, carrying the generator's
  own sentence for the refusal, until a format is picked.

- **A feature the location does not run is stated, and offers no controls.** It
  used to be half-configurable, and reachable enough to block a bundle nobody
  meant to change. Entering an identity by hand, declaring it performance and
  then flipping the **Service virtualization** switch seeded an ingress behind
  empty subdomain and TLS fields, and the step went red with *needs attention*
  for something nothing on the page had asked for. Connect mode had the mirror:
  the card body was inert, so a restored session or an imported profile carrying
  `sv_ingress` opened the group and left a switch that could not be pressed
  back. Such a card now names the feature, says where it is enabled, and shows
  nothing to press — and the options it would have configured are cleared rather
  than merely hidden, so nothing blocks a download from off screen.

- **The line saying why a step is not finished names only fields that are on
  screen.** It was a fixed sentence — "namespace, service account and any
  unfinished group first" — and a docker bundle has neither a namespace nor a
  service account, so two thirds of the only prompt telling you what to fix
  pointed at fields deliberately not on the page. It now names what is actually
  outstanding, and the step's tick is the same derivation, so the two cannot
  disagree.

- **A docker bundle is no longer refused over a field it ignores.** An unnamed
  service account, a malformed `engine_cpu_limit`/`engine_mem_limit` and a
  leftover CA ConfigMap beside an inline PEM each blocked generation for
  `--format docker`, over options the same format states it cannot carry — and
  in the UI those fields are not on screen, so there was nothing to correct.
  A docker bundle's README also no longer advertises an engine size it does not
  set. `crane_hook` and `registry_auth` join the ignored list: both reached
  nothing already and said so nowhere.

- **`bzm-opl-gen plan --users N`: how much infrastructure a load target needs,
  before any of it exists.** Every other command here starts from something that
  already exists — a location, an agent, a cluster, an evidence file. This one
  starts from a number somebody has in a planning meeting, and takes **no API
  key, no facts file and no cluster**, because the customer who needs it most has
  none of them: the cluster is a ticket they have not raised yet, and this is
  what they raise it with.

  ```
  bzm-opl-gen plan --users 5000 -o ./plan
  ```

  5,000 virtual users → 10 engines of 2 CPU / 8Gi → 10 nodes of 3 vCPU / 10Gi
  capacity, a peak of 30 vCPU / 100Gi that idles at zero between runs, one small
  always-on node for the agent, the egress hosts a firewall rule needs, and the
  four BlazeMeter-side settings (`slots`, `threadsPerEngine`, `overrideCPU`,
  `overrideMemory`) without which the cluster is provisioned and then not used.

  **One vocabulary, BlazeMeter's own:** a location holds agents, an agent runs
  engines, and each engine drives virtual users. `slots` and `threadsPerEngine`
  appear only as the names of the two location *fields* they are — concurrent
  engines, and virtual users per engine — rather than as terms anything is
  explained in. The document says nothing about *what* is being tested: the
  request is for capacity to run load tests from this cluster, and naming an
  application invites the reply that it should be sized per application.

  `-o DIR` writes **`capacity-request.md`** — the same numbers written for a
  platform team that has never heard of BlazeMeter, showing the arithmetic so
  the request can be *checked* rather than only read. `--markdown` prints it,
  `--json` gives the whole plan as data.

  **The virtual-users-per-engine figure is an assumption, and everything says
  so.** How many virtual users one engine carries is a property of the script,
  not of the engine — a chatty API test with no think time exhausts one far
  sooner than a browsing journey does — so unset, `--vus-per-engine` assumes what
  an engine of the chosen size is *rated* for (500 for 2 CPU / 8Gi, scaled
  linearly on whichever of CPU and memory is tighter for any other size) and the
  plan carries `vus_per_engine_assumed`. It follows the engine size rather than
  sitting at a flat 500: on the Small preset a flat 500 assumed load the engine
  cannot carry — and then warned about the figure the planner itself had chosen —
  and on Large it asked for twice the nodes needed. The document leads with it, the web panel shows
  it as a callout, and the MCP tool's description tells a model to pass the
  qualifier on. The honest sequence is plan → provision small → measure →
  re-plan, and the document says that too.

  The same calculator is in all three surfaces: **`plan`** in the CLI, a
  **Plan capacity** view in the web UI (a view rather than a step, since
  everything step 1 asks for is what somebody sizing a cluster has not got yet),
  and **`opl_plan capacity`** on the MCP server, which returns the numbers and
  the document together. In the UI, *Use this plan* fills in the location's
  concurrent engines and virtual users per engine, and the bundle's engine size;
  it writes nothing to BlazeMeter. Full reference in
  [docs/capacity-planning.md](docs/capacity-planning.md).

  **None of the BlazeMeter side waits for the cluster**, and the document says
  so: a location and its agent are records in BlazeMeter, so both can be created
  with the planned settings while the infrastructure request is still being read.
  An agent that has never sent a heartbeat is the expected state until its
  manifests are applied, not a half-finished setup — so the wait for nodes is
  setup time rather than dead time.

  `doctor` and the planner now share the virtual-users-per-engine ratio
  (`plan.supported_vus`) rather than each carrying it, so a plan the preflight
  would then warn about cannot be produced.

- **Change a location's settings from the web UI, after it exists.** The
  correction that follows a setup: a location and its agent are built for 500
  virtual users an engine, a real run says the figure is 1,000, and until now
  the only answer was "go and edit it in BlazeMeter" — the one place this tool
  otherwise never sends you.

  Step 1 now edits the selected location's **concurrent engines** (`slots`),
  **virtual users per engine** (`threadsPerEngine`) and the engine's CPU and
  memory **requests** (`overrideCPU` / `overrideMemory`). None of those four is
  in a manifest, so a change needs no regenerate, no re-apply and no restart —
  it applies to the next test that starts, which the panel says.

  **The answer is a re-read of the location, not an echo of the request.**
  BlazeMeter's own create endpoint accepts `threadsPerEngine` and does not store
  it — that is why a freshly created location 403s every test start — so a form
  that reported what it sent would show a number the account never took. Fields
  that came back unchanged are reported as not stored, in amber, beside the ones
  that saved.

  **`slots` is engines per agent, and the calculator divides by them.**
  BlazeMeter's UI calls the field "Engines per agent" — "the number of
  engines/tests that can run on one agent" — so a location's concurrency is
  `agents × slots`. The planner had it as the location's total, which on a
  two-agent location asks for twice the engines and twice the cluster. It now
  takes the agent count (the UI defaults to the number the location has), sets
  `slots` to the run divided by it, and reports **nodes per agent**, because one
  agent is one cluster and the infrastructure request is for one of them. The
  field is labelled "Engines per agent" throughout, and `doctor` — which
  measures one cluster, so was right all along — says "engine(s) per agent" too.

  **The settings open out of the location, and size themselves.** Selecting a
  location expands it the way an agent row does, and the settings are inside it
  — they belong to the one that is selected and to nothing else. **Calculate**,
  beside the heading, sizes *that* location from a virtual user target, starting
  from what it already says: 5,000 virtual users at the 50 an engine a location
  currently advertises is 100 engines and 100 nodes, which is the argument for
  changing the figure rather than the pool.

  It is guidance, not a form. It answers in engines, **nodes** and peak vCPU —
  the cost that lands off this page, on a cluster nobody sees from here — flags
  the users-per-engine figure as an assumption when nothing supplied one, and
  carries the same warnings the planner does. *Apply* fills concurrent engines
  and the two engine requests; applying is not saving, and **Save** is still the
  only control that writes.

  Step 1's three sections (Connect, Private location, Agent) are now bordered
  panels with tinted headers, and they fold: a chevron on the left of the
  header, pointing right when closed and down when open, and the whole bar is
  the control. They open on whichever section the step has reached until one is
  pinned, and a folded one says on its header what it holds. Three sections
  divided by a hairline on one white background read as a single long form; a
  panel's extent is the thing a reader needs before anything inside it.

  Only changed fields are sent, so a page left open does not write back three
  values somebody else has since edited; blank means "leave alone", so there is
  no way to *clear* a setting here; and `funcIds` is deliberately not in the set
  (it is `add_func_id`'s, which is additive by construction — a passthrough
  would let a caller replace the whole list by accident). This is the third and
  last write the page makes to a customer's account, and like the other two it
  is a control of its own that says what it costs first.

### Fixed

- **A generated bundle now says what the location must be set to.** The bundle
  deploys an agent and cannot set the location, and neither `slots` nor
  `threadsPerEngine` is in a manifest — so a bundle handed to a colleague
  deployed cleanly, the agent came online looking healthy, and the first test
  start failed with 403 *Not enough available resources*, with nothing in the
  43-line README mentioning it. The README states both figures when they were
  read, and says to check them when they were not.

  It does **not** say *why* they are unknown, and that is the point: facts typed
  in by hand carry no location settings because there was no account to ask,
  and a location that genuinely has neither reads the same. Only `doctor` may
  tell those apart. A test asserts over the parsed source that `generate` never
  reads that marker — the manifests are identical however the facts arrived,
  which is the property manual entry exists to preserve.

- **A refresh while the server is down no longer loses which location and agent
  you were on.** The page already waited for the connection check before writing
  its session back, so the empty starting state could not overwrite what it was
  about to restore — but a check that *failed* released that guard anyway, and
  saved nulls over the account, workspace, location and agent ids one tick
  later. A failed check means the account could not be asked, which is not the
  same as the location being gone: the ids are kept and the next attempt — a
  reload, or connecting a key from the page — re-selects them. Each is still
  applied only where the account confirms it still exists, and one deleted from
  the account is written away the moment the list says so.

- **The account listing was truncated at 100 workspaces, and two fifths of a
  real account went missing with it.** `/workspaces` was asked for the first
  100; the account it was measured against has 166, and the 66 that fell off held 105,270 rated
  virtual users across 52 locations — including the account's largest workspace,
  which had no card on the Account capacity page at all. Locations were already
  asking for 1,000; workspaces do now.

- **`slots` in a plan is engines per *agent*, and the document says so.** A
  location's concurrency is agents x engines per agent, and a plan that read
  `slots` as the whole run told a four-agent location to set four times the
  engines it needs. `bzm-opl-gen plan --agents N` divides by it, and the pane
  inside an existing location reads the count off the location.

  **Plan capacity does not ask how many agents you will have.** It was a field
  for one release and should not have been: the panel exists for somebody with
  no cluster, and how many agents they end up running is decided afterwards and
  changed at will. Asked up front it is a guess that silently halves or doubles
  `slots`. One agent is what it sizes for, and the request document says to
  multiply if you add more.

- **The planner's "virtual users per engine" suggestion follows the engine size
  before a target is typed.** It was read off the last plan, so choosing Large
  and reading the field showed BlazeMeter's 500 — the standard engine's figure —
  next to an engine rated for 1,000. It now asks the server what the chosen size
  is rated for, which is what the plan will assume.

- **A location with no rating is counted as unknown rather than as zero.** The
  Account capacity header says how many locations have no engines-per-agent or
  no virtual-users-per-engine set. The number was already served and never
  shown, so the page quietly rounded an unanswered question down to nothing.

- **The generated request document called `slots` "concurrent engines".** It is
  engines per *agent* — BlazeMeter's own label — and the row now says so, with
  the multiplication spelled out (`4 x 3 = 12 engines`).

- **The web UI stops re-reading the account on every page load.** Accounts,
  workspaces, locations and an agent's facts are held for 60 seconds by the
  server, which turns a reload from four BlazeMeter round trips into one local
  one — measured on a real account, the location list alone went from 1.29s to
  0.04s. Every write this server makes (a new location or agent, a settings
  change, a feature enabled, a different key) drops the cache, so the staleness
  it can produce is never your own change. An agent's heartbeat is deliberately
  not cached: the status poll is what says an agent came online.

  It lives in `server.py` rather than `core.py` on purpose. This process is one
  browser session holding one client, so its own writes are the only changes it
  can miss. `core` is also the MCP server's, which is long-lived and whose
  caller has other ways to change the account — a cache there would answer
  "list the locations" with one that has since been deleted.

- **The location/agent summary moved out of step 1 and under the flow.** It was
  a line between the Connect and Private location panels, where it read as a
  divider between two sections rather than as the result of all three. It is now
  a footer under the step — outside the scrolling area, so it does not move, and
  present on every step, because "which location and agent am I generating for?"
  is as much a question in Configure and Download as in Agent details.

- **The account and workspace dropdowns say when they are loading.** Both are a
  round trip to BlazeMeter over whatever network the user is on, and both were
  silent while it happened — an empty dropdown and a slow one look identical, so
  the answer to "my account is not in the list" was to wait and try again. They
  now show `loading…` and a small spinner inside the field, and cannot be
  cleared or opened until the options arrive.

- **A collapsed step-1 section kept its controls clickable.** The body stays
  mounted while folded so what was typed into it survives, but a mounted body
  inside a zero-height row is still in the hit-testing and accessibility trees:
  its buttons took clicks aimed at whatever was drawn over them, and a keyboard
  tab walked into a section nobody could see. Folded sections are now
  `visibility: hidden` as well as zero-height, which keeps the state and takes
  them out of both.

- **An API call the running server has never heard of now says so.** The UI
  bundle is read from disk on every request, so a server left running for a day
  serves a page whose calls it cannot answer — FastAPI has no route, the SPA's
  static mount answers the POST with `405 Method Not Allowed`, and a working
  feature looks broken. A 404 or 405 carrying no `detail` is exactly that case,
  and the page now reports it as "this page is newer than the server it is
  talking to" with the command to restart, rather than as the feature's own
  error.

- **The web UI's engine-sizing hint still claimed engine requests could not be
  set.** "Crane stamps them at 250m/256Mi and the scheduler packs nodes on
  those" was the belief a live GKE run disproved in the previous release — the
  bundle sets the engine's *limits*, the location's `overrideCPU`/
  `overrideMemory` set its *requests*, and 250m/256Mi is only the default for a
  location that sets neither. The correction reached the generator, the node
  pool recipe and `doctor`; this hint was missed, so the one place a user
  configures engine size still told them the fix was unavailable.

### Changed

- **BREAKING: `generate --api-key` no longer fetches an AUTH_TOKEN.** It fetched
  one before, and that fetch *mints*: BlazeMeter issues a fresh token and
  invalidates the previous one. So regenerating a bundle — even just to look at
  it — revoked the credential of the agent already running from the last one, and
  it failed silently. Crane answers a dead token with `404`, logs `Sleeping for
  300`, and never starts its health service, so the pod sits `0/1 Running` and
  reads as a slow boot rather than a revoked credential. That cost a live
  debugging session before the cause was found.

  Two harms, and neither is secrecy — crane logs the token in plaintext, and
  anyone who can read a pod log in that namespace can read the Secret anyway.
  The first is **permanence and reach**: a token in a model transcript or a
  shared `profile.json` reaches people who never had access, for as long as the
  file exists. The second is **rotation**, above.

  `generate` now resolves the token in four steps, prints which one it took, and
  reaches BlazeMeter only in the second:

  1. `--auth-token <token>` wins outright.
  2. `--rotate-token` (new, and needs `--api-key`) issues a new one. It warns
     before it acts, naming the ship and the consequence. There is no
     confirmation prompt — the flag is the confirmation.
  3. Otherwise the token already written into `-o` is read back and reused,
     provided that bundle's `profile.json` names the same `ship_id`. This is what
     makes regenerating a bundle byte-identical.

     If `-o` holds a bundle for a *different* ship, or one whose `profile.json`
     cannot say whose token it is, **the command refuses and writes nothing.**
     Generating there would overwrite that bundle, and its AUTH_TOKEN cannot be
     fetched again afterwards — the only endpoint that returns one issues a new
     one — so it would survive nowhere but inside an agent still running on it.
     Pass `--auth-token` (or `--rotate-token`) and the directory is not consulted
     at all, which is how you replace such a bundle deliberately.
  4. Otherwise the `<YOUR_AUTH_TOKEN>` placeholder stays, and the command names
     the two places a real token comes from — what `create-ship` printed, or
     `kubectl -n <ns> get secret blazemeter-secret -o
     jsonpath='{.data.AUTH_TOKEN}' | base64 -d` for an agent already deployed.
     That command is printed, never run: nothing here reads your cluster.

  **What to change:** anywhere you ran `generate --api-key`, pass `--auth-token`
  instead, or drop the flag and let the bundle in `-o` supply its own token. On
  its own `--api-key` now warns that it has no effect. `out/profile.json` still
  does not carry the token and will not start doing so.

  `create-ship` is unchanged except in what it says: the token it prints is the
  durable artifact, nothing here records it, and the `next:` line it prints now
  passes `--auth-token` rather than `--api-key` (which would produce a
  placeholder bundle).

  **BREAKING on the MCP surface: `opl_bundle generate`'s `fetch_token` argument
  is now `rotate_token`, and defaults to `false`.** It defaulted to minting, so
  every generate revoked the running agent's credential — and a session there has
  no terminal to be warned in and no prompt to be stopped at. The rename is the
  safeguard: a model reads the argument name, so the argument name has to be the
  warning. `fetch_token` is *refused* rather than ignored, because a caller
  working from a cached description means to mint and a silently-placeholder
  bundle is a worse answer than one round trip.

  Every generate now reports `token_source: {branch, ship_id, message}` — one of
  `given`, `rotated`, `reused`, `placeholder` — and a rotation is repeated in
  `warnings`, naming the ship whose credential was replaced. A live rotation on
  this surface used to answer `warnings: []` and name nothing at all. The token
  itself still never appears in a response, on any branch. `generate` also
  passes `out_dir` through now, so an MCP session reaches the `reused` branch:
  regenerating into a directory that already holds this ship's bundle issues
  nothing and comes out byte-identical. It could not reach that branch before.
  `opl_location reveal_token` is unchanged — the sanctioned way to read a token,
  and a whole action so it cannot happen as a side effect.

  **The web UI follows the same rule**, and the button that used to break a
  running agent was the download: it fetched an AUTH_TOKEN on the way out, so
  taking a copy of the bundle to read it rotated the credential of the install
  already running. Downloading and **Save to folder** now mint nothing, and both
  say which of the four ways their bundle got its token.

  Where the token comes from instead: **creating an agent issues it once, there,
  and puts it in a field on the page** — a ship created a moment ago has no
  previous credential to invalidate, which is why that is the one action that
  still fetches. The field is masked with a *Show* toggle, and nothing writes it
  down, so that page is the copy to keep.

  **Pointing at an agent that already exists leaves the field empty**, because
  no API reads an existing token back. Paste what you kept, or tick *Issue a NEW
  AUTH_TOKEN with this bundle* — which says, before you download, that it kills
  the credential the running agent holds. A download with neither is a
  placeholder bundle, and the page says so over the button rather than leaving
  you to find out at `kubectl apply`.

  **Saving twice into the same folder no longer rotates.** The bundle already
  there supplies its own token — same folder, same ship, same bytes — so
  re-rendering with one option changed leaves the agent deployed from the last
  save working.

- **`livetest` issues one credential per run instead of one per render.** Its
  regenerate step called the token endpoint every time it was invoked, and a run
  invokes it three or four times — the negative control renders twice, then
  `--run-test` and `--local-proxy` each do — so a single run minted four
  credentials, each invalidating the last. Any agent deployed from an earlier
  render was holding a revoked token and sat `0/1 Running`, which is
  indistinguishable from a slow boot; this is plausibly a real source of the
  rig's intermittent failures. One token now, minted at the start, printed with
  the ship it was for, and threaded through every render.

  There is no `--rotate-token` on this command and there should not be: bringing
  an agent online is its entire purpose, so the rotation is implied by running
  it. New `--auth-token` skips the mint for a caller already holding one — the
  token `create-ship` printed, say.

### Added

- **`--sv-ingress none`, for a location that offers service virtualization when
  you only want performance.** A location carrying `mockServices` was refused
  without an ingress, full stop — and plenty of accounts have locations carrying
  both funcIds because somebody enabled them together, then run nothing but
  tests on them. In the web UI the Service virtualization switch was marked
  *required* and snapped straight back on, so there was no bundle to be had at
  all.

  The refusal stays, because unset means nobody answered and the failure it
  catches is invisible on a cluster. `none` is the answer: the bundle is the
  performance one — no ingress, no SV RBAC, no TLS secret — and `--format helm`
  works again, since there is nothing left for the chart to be missing. What it
  costs is stated rather than hidden: deploy a virtual service to such a
  location and it stalls at `WAITING_FOR_DOMAIN`, which is what the refusal was
  protecting you from. The images do not change — which set the agent runs is a
  fact about the location, not about this option.

  In the UI the switch now turns off, the row reads *declined* and says what was
  given up, and `profile.json` records `sv_ingress: none`, so re-importing a
  bundle does not land back on the refusal.

- **`bzm-opl-gen mcp` — an MCP server**, so an AI session can do the whole OPL
  deployment without a checkout of this repo: find the location, read its real
  image references, preflight a cluster from an evidence file, and write the
  manifests. `pipx install 'bzm-opl-gen[mcp]'`, then point your client at it —
  copy-paste config in [docs/mcp.md](docs/mcp.md).

  Five tools, each dispatching on an `action`, matching the shape the sibling
  BlazeMeter MCP servers already use: `opl_location`, `opl_facts`, `opl_bundle`,
  `opl_preflight`, `opl_agent`. The reference pages ship with the wheel and are
  served as resources, so a session can read the options table rather than guess
  at an option name.

  `opl_location list` answers one line per location and the first 50 of them,
  narrowable by `name_contains` and `limit`, with `show` for the agents of the
  one you pick. An account with 171 locations and 221 ships listed in full came
  to 84,779 characters — past a client's result ceiling, truncated to a file and
  never read, which blocked every step behind it. Anything the cap or the filter
  leaves out comes back as a count: a list that quietly stopped would read as
  the whole account.

  Three things it will not do. **The AUTH_TOKEN never appears in a response** —
  `generate` writes the Secret and answers with file names and byte counts, and
  reading a bundle file back redacts the token rather than handing it over,
  because a response is transcribed, summarised and quoted back, and this
  credential rotates every time it is issued. `reveal_token` is the one way to
  get the value, and it is a whole action so it cannot happen by accident;
  `generate` issues one only when asked, by name (see `rotate_token`, above).
  **A secret is never a tool argument** — passing `auth_token` in the options is
  refused rather than written; a path may be named, and the key itself comes
  from the server's environment. Files are named the same way: `opl_preflight`
  takes `evidence` as the path of the cluster-evidence JSON the customer sent as
  readily as the parsed object, so a session need not read several KB of node
  lists aloud to preflight one. **Nothing applies to a cluster** — `kubectl
  apply` stays in your shell, where you can see what is being applied. The one
  exception is `opl_agent livetest`, which deploys because that is all it does,
  and which is off by default.

  `opl_location delete` needs `BZM_OPL_ALLOW_DESTRUCTIVE=1` and `opl_agent
  livetest` needs `BZM_OPL_ENABLE_LIVETEST=1` — separate variables, because
  enabling one should not quietly enable the other. Both are read when the
  action runs, so setting one does not mean restarting your client. Image
  mirroring is annotated destructive but not gated: it adds images to a
  registry you named, where the worst case is repositories nobody wanted.

### Changed

- **`ui --dev` now detects a `BZM_API_KEY_FILE` set after startup.** The four
  paths an `api-key.json` is looked for were frozen at import, and `--dev` sets
  that variable for its reloader subprocess — which worked only because the
  subprocess re-imports. Read per call now. Nothing else about key detection
  changed, and the secret is still never read back out; only the key id is.

  This is the one behaviour change in an otherwise internal split: what the
  tool *does* moved to `bzm_opl_gen/core.py`, which imports no web framework,
  and `server.py` is the HTTP layer over it. Nothing in the API moved, and the
  same status codes come back from the same routes. It matters here because the
  ship a token is fetched for was decided in three places — the UI's download
  button, `generate --api-key` and `livetest` — and fetching a token rotates it,
  so the three disagreeing would have meant rotating a credential belonging to
  an agent nobody mentioned. One rule now, in one place.

- **Python 3.10 is now the floor**, up from 3.9 — which has been end-of-life
  since October 2025. The generator itself uses nothing newer; the bump is the
  `mcp` SDK's requirement, and it is being made now so the MCP layer lands on a
  supported floor rather than shifting it later.

- **`docs/options.md` is generated.** Every option now has a row, grouped by
  what it configures, where ten of the thirty-one keys previously had no
  documentation at all — including `namespace`, `run_as_user`, `tolerations`,
  `node_selector` and all three ephemeral-storage settings. The descriptions
  live in `bzm_opl_gen/options.py` and the doc is rebuilt from them with
  `python -m bzm_opl_gen.options`; editing a table cell by hand now fails the
  test suite, which also fails if an option is added to the generator and not
  to the registry. The prose sections around the table are still hand-written.

  The three CA options and the two engine-limit options used to share a table
  row each, which is why `ca_configmap_key` had nowhere to be documented; the
  "pick exactly one" that grouping carried is now stated above the section.

### Fixed

- **`images --pull` runs at all.** It raised `NameError: name 'dry' is not
  defined` on every invocation, with or without `--mirror`, with or without
  `--dry-run` — a guard at the end of the command tested a name that never
  existed, and it was evaluated unconditionally once `--pull` was given. Behind
  it sat a second bug: a `subprocess.run` on the loop variable *after* the loop,
  which would have re-run the last command on its own. Both are gone;
  `core.mirror_images` was always the thing doing the pull/tag/push, and the loop
  in the command only reports what it did. Nothing covered this path, which is
  how a crash on the happy path survived. Plain `images` (listing only) was never
  affected, and neither was the MCP `opl_bundle images` action.

- **An account that refuses to issue an agent credential now says so, and says
  what still works.** Some accounts serve the token endpoint only from
  BlazeMeter's own gateway and answer everything else `403 Forbidden: Should
  access from Private-Data gateway`. That raw body used to be the whole message:
  it names no ship, does not distinguish "the credential could not be issued"
  from "your request was wrong", and offers no way on — so `generate --api-key`
  and `create-ship` dead-ended on an account whose only real problem is that
  tokens have to come from the UI. The refusal now names the location and the
  ship, says which half failed, and points at `--auth-token`, which also stops
  the token being rotated. The upstream reason is still quoted.

- **A refusal on the command line is a sentence, not a traceback.** Anything
  `bzm-opl-gen` refuses deliberately is written for the person who ran the
  command, and `generate` had no guard around it — so on a refusing account the
  message above arrived under seventy lines of Python stack, which is a worse
  answer than the raw `403` it replaced. `main()` now renders any deliberate
  refusal and exits non-zero; `create-ship` still catches its own first, so the
  agent it just created is reported whatever the token endpoint answers.

### Added

- **`GET /api/option-docs`** — one line per option, plus its type, whether it
  accepts null, its choices and whether it is a credential. Kept separate from
  `/api/option-defaults`, whose every key is submitted back as an option.

### Changed

- **Crane's Kubernetes auto-updater is now OFF by default**
  (`AUTO_KUBERNETES_UPDATE: 'false'`), in both output formats and in the chart
  standalone. It was on for every bundle without a private registry, copied
  from BlazeMeter's own manual Kubernetes manifest, which ships `'true'`.

  On, it breaks the upgrade path of the thing that installed it. Crane takes
  field ownership of its own Deployment within seconds of install (manager
  `OpenAPI-Generator`), rewriting the image and `.spec.strategy` from `Recreate`
  to `RollingUpdate` — so the next `helm upgrade` fails on a field-ownership
  conflict with the ConfigMap already applied, and `--force-conflicts` cannot
  resolve it: forcing `type: Recreate` back leaves crane's
  `strategy.rollingUpdate` beside it and the API server rejects the pair.
  Changing anything meant uninstall + install. The documented fix was a value
  you had to set *before* installing, which nobody knew to do until the upgrade
  that failed. That is the whole reason for the change: a default that breaks
  its own upgrade path is not a default.

  **What it costs, and it is real:** the agent no longer updates itself.
  Keeping it current is now your job — re-generate and re-apply, or bump
  `image.tag` and `helm upgrade` — and an agent that falls far enough behind
  loses BlazeMeter support. Generated bundles say so: the ConfigMap, both
  READMEs and the chart's `values.yaml` all state it where the value is set.

  **To keep the old behaviour**, generate with `--auto-update` (option
  `auto_update: true`, "Agent auto-update → On" in the UI, `autoUpdate: true`
  in the chart), knowing upgrades then mean uninstall + install. Existing
  clusters are untouched until you re-apply; a `profile.json` from before this
  change has no `auto_update` key and so re-generates with the new default —
  re-apply the ConfigMap and restart crane to actually turn the updater off on
  a running agent.

### Added

- **Auto-update is now an option, in both output formats and the UI.**
  `AUTO_KUBERNETES_UPDATE` was decided entirely by the registry, with no way to
  say otherwise short of editing the ConfigMap after generating.
  `--auto-update` / `--no-auto-update` (option `auto_update`, "Agent
  auto-update" under Security & RBAC in the UI, `autoUpdate` in the chart) now
  set it either way, which is what made the default above a choice rather than
  a removal. The generated README gives whichever upgrade instruction matches
  the bundle, instead of one instruction that was wrong for half of them.

  This is BlazeMeter's Kubernetes auto-updater. Their `AUTO_UPDATE` is a
  different variable — documented as the Docker-side switch, inert on a
  Kubernetes agent — and nothing this generator emits sets it.

### Changed

- **Engines drop privileges on every platform, not just OpenShift.** The two
  ConfigMap keys that make crane stamp a security context on the pods it spawns
  — `INHERIT_RUNNING_USER_AND_GROUP` and `KUBERNETES_SECURITY_CONTEXT_CAP_JSON`
  — were emitted only for `platform=openshift`. Since `platform` defaults to
  `openshift`, the restricted engine was already what most bundles got; naming
  `k8s` quietly opted out of it and left crane's own default, which is a
  *privileged* engine pod. Restricted PodSecurity, OpenShift's restricted-v2 SCC
  and GKE Autopilot's Warden all refuse that — and refuse it after the agent is
  online and the location reads ready, so the run hangs at `BOOT_STARTING`
  rather than failing usefully. Nothing in those keys was ever
  OpenShift-specific. Verified against the images that have to tolerate it —
  the taurus engine, the doduo grid proxy and a charmander browser pod all
  observed from inside a running container with every capability set zero, and
  none of them needing one; see **Added** below. `--no-restrict-engines`
  restores the old behaviour for an image that genuinely needs a capability, at
  the cost of the posture on every container crane creates. `doctor` follows the
  option rather than the platform: `pod-security.kubernetes.io/enforce=restricted`
  is now a PASS, and a FAIL only when the restriction is turned off.

### Fixed

- **Browser images from a live GUI location now name repos that exist.** A
  Kubernetes agent reports them as keys with a path of their own —
  `blazemeter/charmander/chrome_136.0.7103.113` — and only the last segment was
  kept, so the repo came out as `.../blazemeter/chrome_136.0.7103.113`, which
  404s. With a private registry that is the failure the registry was configured
  to prevent: the mirror script pulls nothing, or, if the mirroring was done
  separately, `IMAGE_OVERRIDES` sends crane after an image nobody pushed and the
  location dies mid-test on an `ImagePullBackOff`. Losing `charmander` from the
  repo also cost the images their category — they came back `performance`, so a
  performance-only location selected four browsers it has no use for. Both are
  fixed by stripping only the redundant `blazemeter/` prefix. Flat keys and the
  irregular ones (`taurus-cloud`→`v4`, `blazemeter`→`v3`) are unchanged.
- **The crane pod now asks for the ephemeral storage it actually uses, and asks
  for it as one number.** The request was `100Mi` against a `1Gi` limit; crane
  reaches ~161MiB (107MiB of it `/tmp`) within seconds of starting, so the
  request never described the pod on any platform — elsewhere only the limit
  kept it alive. On GKE Autopilot, which rewrites the ephemeral-storage limit
  down to the request, the pod came back `100Mi/100Mi` and was evicted about
  twelve seconds into every start, indefinitely. Both fields are now `1Gi`, and
  `--crane-ephemeral-storage SIZE` moves them together — one value, because a
  gap between them is headroom on some platforms and a silent ceiling on
  others. CPU and memory are unaffected and unchanged.

### Added

- **[docs/hardened-engines.md](docs/hardened-engines.md) — which images have
  actually run under the hardened default.** The posture is a property of the
  pod spec, so re-running it on another cluster proves little; what varies is
  the image. Each image crane makes a pod from is recorded there with what was
  read *inside* a running container, including a browser pod driving a real
  Selenium session and an OpenShift run where the SCC, not `run_as_user`,
  assigned the UID. Nothing needed a capability — which matters before reaching
  for `--no-restrict-engines`, since it drops the posture for every container
  crane creates, not for the one image that wanted something.
  [docs/repro/hardened-posture-probe.yaml](docs/repro/hardened-posture-probe.yaml)
  re-runs the image half against any tag, with no account and no crane.
- **Choose the ServiceAccount the agent runs as, and whether to create it.**
  `--service-account <name>` (default `crane`, so existing bundles are
  unchanged) names the account the Deployment runs as and the one the
  RoleBinding — and the ClusterRoleBinding, with `--cluster-rbac` — grants to.
  `--no-create-service-account` leaves the ServiceAccount object out of the
  bundle for an account your platform team already owns; everything still
  references the name you gave. Both are in the web UI beside the namespace,
  and in the Helm chart as the `serviceAccount.create` / `serviceAccount.name`
  values it already had.
- **`scripts/bzm-cluster-evidence.sh`** — a read-only script to hand a customer
  whose cluster you have no access to. They run it, and one JSON file comes back
  carrying what a deployment has to be shaped around: nodes, ingress classes,
  the namespace, its LimitRanges/quotas/ServiceAccounts, which ingress API
  groups the cluster serves, the OpenShift ingress domain and cluster proxy, and
  `auth can-i` answers for everything the bundle applies. It is the cluster-side
  twin of `facts --manual`. Secrets are listed by name and type only — never
  `-o json`, so no secret value is ever in the output — and ConfigMaps by name,
  which also keeps a 300KB CA bundle out of the file. Anything unreadable is
  recorded as `null` with the error rather than as an empty list, because
  "denied" and "there are none" are different answers and `doctor` treats them
  differently.
- **`doctor --cluster-evidence <file>` preflights a cluster you have no access
  to**, from the JSON that script produced there — no cluster reachable, no
  kubeconfig configured. It runs the same checks over the same data and prints
  the same verdict list: the file carries the `kubectl get` documents `doctor`
  would have read, and they are normalised into exactly what the live path
  gathers, so nothing downstream knows which way the data arrived. The namespace
  defaults to the one the evidence was collected for, and preflighting a
  different one is reported rather than quietly used. Two things are reported as
  unverified rather than guessed: egress, which needs a pod inside the namespace
  to curl from, and any section the script was refused — those stay WARN ("we
  did not look") instead of becoming the FAIL an empty list means ("we looked,
  there are none"), so a file collected with little access exits 0 with warnings
  rather than a false alarm. A file whose `schema` is missing or unrecognised is
  refused by name.
- **`bzm-opl-gen suggest --cluster-evidence <file>`** — what a cluster's
  evidence implies about the generate options, with no cluster and no API key.
  `doctor` asks whether a deployment would survive a cluster; this answers the
  question that comes first, and writes the reasoning down instead of leaving it
  in whoever read the file. Each suggestion names the evidence behind it and how
  strongly it holds: **decisive** (the namespace already holds the ServiceAccount
  the bundle would create) or **suggestive** (the served API groups rule some
  `sv_ingress` values out without picking among the rest — narrowing to one
  survivor is still not choosing it). Covers `platform`, `service_account_create`
  / `service_account_name`, `sv_ingress`, `sv_subdomain`, `pull_secret`,
  `ca_existing_configmap`, `proxy`, `ca_openshift_inject` and `cluster_rbac`.
  Nothing is applied; `--json` emits the same as data.
- **Cluster preflight in the web UI.** Pick the file the collector wrote and see
  `doctor`'s verdicts against the configuration on screen, re-run as you edit it.
  `POST /api/preflight` needs no API key and no kubecontext — the same "no access
  to anything" path manual facts entry serves. The panel header says what was
  imported — collected when, the namespace the file *describes* as against the
  one being preflighted, and every section the collector was refused — and the
  same facts lead the verdict list, so a thin file cannot read as a clean bill
  of health. A file that is not evidence is refused by name and leaves the
  verdicts already on screen standing.
- **Apply what the evidence implies, one suggestion at a time.** Decisive
  suggestions offer their value as a single click; suggestive ones offer a button
  per candidate and never a default. **A value you already set is never
  overwritten silently** — the row turns amber, shows both values and the
  evidence behind the suggestion, and the button says *Replace*. Applying is
  reversible for the session, and an applied value is an ordinary option from
  there on: the bundle and `profile.json` are identical to what typing it gives.
- **`doctor` checks that an existing service account is really there.** Only
  when the bundle does not create one. Nothing fails at apply time if it is
  missing: the Deployment is accepted, no pod is ever created, and the reason
  is an event on the ReplicaSet.

### Fixed

- **The Helm chart no longer refuses `serviceType: NODEPORT` without
  `clusterRbac: true`.** The refusal rested on crane resolving its advertised
  address from the cluster-scoped Node object and falling back silently to
  `127.0.0.1` when denied. A live performance location on crane 3.7.55
  disproved it: deployed with NODEPORT and namespaced RBAC only — no ClusterRole
  in the cluster — the agent came online, crane created its NodePort Service
  through the namespaced Role, and a real engine ran a test to `ENDED`. Crane
  takes the address from its own network interfaces, and nothing in its log was
  forbidden. Corrected in `clusterrole.yaml` (both formats), the chart's
  `values.yaml` and its README; cluster-scoped node reads remain genuinely
  optional, for capacity awareness. The parity suite now covers NODEPORT
  *without* cluster RBAC — the combination the two formats disagreed on was
  tested in neither direction, which is how the disagreement survived. (#49)
- **`doctor` no longer fails a manually-entered location for `slots` and
  `threadsPerEngine`.** With no account to read them from, both are now reported
  unknown, naming Settings → Private Locations. A location gathered from the
  account with either genuinely unset still FAILs with the 403-at-start wording:
  the two are told apart by the `images_source` marker the facts already carry,
  so generated manifests are unaffected and nothing else downstream learns how
  the facts arrived. The no-account, no-cluster path — manual facts plus an
  imported evidence file — previously reported two failures for values nobody
  could have supplied. (#55)
- **`scripts/bzm-cluster-evidence.sh` no longer claims the cluster-scoped
  permission rows decide whether `serviceType: NODEPORT` is available.** Crane
  resolves its advertised address from its own network interfaces, not from the
  Node object, and NODEPORT has run green against a cluster where the agent had
  namespaced RBAC only — see #49. `suggest` will not draw that inference either.
- **Nothing is suggested from evidence the collector could not read.** A `null`
  section is skipped, but that alone is not enough: `auth can-i` and
  `api-resources` both report failure as *no*, so a file collected with no
  kubeconfig reads at face value as a plain Kubernetes cluster where nothing may
  be created — and would have produced `platform`, `cluster_rbac` and
  `service_account_create` about a cluster nobody described.
  `versions.serverVersion` is present only when a server actually answered, and
  without it `suggest` returns nothing and says why. `doctor` still reads such a
  file usefully: a warning about what could not be seen is worth having, a
  configuration guessed from it is not.

### Changed

- **Service virtualization: `--service-type NODEPORT` is now allowed with
  `--sv-ingress nginx` or `openshift`, and still refused with `contour` or
  `istio`.** It used to be refused for every backend, on the reasoning that
  NODEPORT forces a cluster-scoped Node read a namespaced Role cannot grant.
  That reasoning was wrong — crane's Node read is denied under NODEPORT on all
  four backends and two of them publish fine anyway. What actually decides it is
  the port crane writes into the object it publishes: `nginx` and `openshift`
  write a constant that stays valid, while `contour` and `istio` take the
  Service's **nodePort**, which nothing reaches the ingress on. Those two fail
  silently — object written, mock `1/1`, endpoint advertised, and contour
  answers 503 while istio's gateway listens on the nodePort alone — so the
  refusal stays for them, now with the measured reason. All four were deployed
  live to settle it. If you use `nginx` or `openshift`, NODEPORT is available in
  the CLI and the web UI, and an imported profile keeps whichever service type it
  arrived with instead of being rewritten to `CLUSTERIP`. Nothing about existing
  `CLUSTERIP` bundles changes. Details, including a crane-free reproduction of
  the contour case, in `docs/service-virtualization.md`.

- **`doctor` no longer fails a cluster for something it was not allowed to
  look at.** A `get` that is denied or errors — nodes, LimitRanges,
  ResourceQuotas, ServiceAccounts — now reports WARN "could not be read" for
  that check, where a denied `list nodes` previously produced the same
  "no eligible node — engines have nowhere to run" FAIL, and a non-zero exit, as
  a cluster that genuinely had none. Reading nothing and finding nothing are
  different answers; only the second is a failure.

- **Helm chart: `serviceAccount.name` is now required when
  `serviceAccount.create` is `false`, and the chart refuses to render without
  it.** It previously fell back to the namespace's `default` ServiceAccount —
  the usual chart scaffold, and wrong here: that installs cleanly and grants
  crane's Role to every other pod in the namespace that runs as `default`. If
  you install this chart with `serviceAccount.create: false` and no name, set
  the name to whichever account you meant. Bundles from `bzm-opl-gen generate`
  always carry an explicit name and are unaffected.

## [0.2.0] — 2026-07-27

Two things you could not do in 0.1.0: install the deployment as a **Helm chart**
rather than flat YAML, and **generate a bundle for an account you have no access
to**. Plus a real fix — `IMAGE_OVERRIDES` could come out empty for a location
with no running agent, which only shows up once the customer's cluster is
actually sealed.

### Removed

- **`sv-bridge` support.** The funcId is retired upstream, so it no longer
  selects an image, no longer appears in the create-location form, and no longer
  makes the service-virtualization ingress options mandatory. Locations that
  still carry the funcId now generate as ordinary performance locations — if you
  mirror images for one, the `sv-bridge` image is no longer in the set.
- **Web UI: the `sv-expose` panel**, and the `POST /api/sv-expose` endpoint
  behind it. It asked for an ingress class most people cannot judge, on a screen
  that appeared whether or not the cluster had the problem it solves. The
  `bzm-opl-gen sv-expose` **command is unchanged**; the endpoint check below is
  what now tells you when you need it.

### Added

- **Helm chart output** — `generate --format helm` emits the same deployment as
  a chart (`out/helm/`, byte-identical for every customer) plus a values overlay
  (`out/bzm-opl-values.yaml`, the only file generated from the account), instead
  of flat manifests. Both formats render the same objects; a parity check renders
  17 option combinations both ways and requires them to agree, so the choice is
  about how you install and upgrade, not what lands in the cluster. Set
  `autoUpdate: false` in the overlay if you intend to run `helm upgrade` — left
  on, crane takes ownership of its own Deployment and the next upgrade fails
  half-applied on a field-ownership conflict. Both behaviours were confirmed
  against a live cluster and a real agent. Service virtualization is refused in
  this format rather than emitted broken, and `livetest` does not take a chart
  directory. See [docs/helm.md](https://github.com/benjithompson/bzm-opl-generator/blob/main/docs/helm.md).
- **Generate for an account you cannot reach** — the three values BlazeMeter
  shows on an agent (harbor id, ship id, AUTH_TOKEN) are enough to render every
  manifest, so a customer's deployment can be produced with access to neither
  their BlazeMeter account nor their cluster. `bzm-opl-gen facts --manual
  --harbor-id H --ship-id S`, or **Enter values manually** in the web UI. Nothing
  is validated and nothing is sent to BlazeMeter. What you give up is listed in
  the README — chiefly that the crane tag floats on `latest`, and that GUI
  browser images cannot be resolved without a live agent.
- **Web UI: the deployed virtual services, beside the heartbeat** — while
  watching an SV deployment, each one is listed with the endpoint host it
  publishes, refreshed on the existing poll. The agent reports idle whether or
  not its virtual services ever became reachable, so a deploy stalled at
  `WAITING_FOR_DOMAIN` used to look identical to a healthy one. Needs a
  kubecontext like `sv-expose` does; without one the panel still watches the
  heartbeat and says why the list is absent.
- **Web UI: configure one feature at a time.** The configure step shows the
  selected feature's options plus the ones that apply to any deployment. It is a
  view, not a scope — the manifests still come from the location's own funcIds,
  so nothing set under another feature is lost or omitted. Options set out of
  view are listed beside the preview; required ones missing from view block the
  download with a link to the feature that needs them. The feature list is
  served, so functional testing, secrets or API monitoring become selectable
  without a UI release.
- **Web UI: picking and creating are separate.** Starting to create a location
  or an agent hides the list of existing ones until you finish or cancel, so it
  is never ambiguous which of the two you are doing — they have very different
  consequences when an agent identity is already running somewhere.
- **Web UI: check whether a published endpoint answers.** Beside the virtual
  services in the watch panel, a check reports the HTTP status or which kind of
  failure it was. A 503 is the diagnosis rather than a broken check: it is the
  cluster refusing crane's port reference, and it names `sv-expose` as the fix.
- **Web UI: the SV prerequisites the bundle does not create** — wildcard TLS
  secret, Istio Gateway, the controller — now say who provides each one and what
  the chosen backend actually does with it, alongside the endpoint host to check
  after applying. Previously README-only, while the failure it prevents is
  silent: manifests apply, agent goes idle, mock runs 1/1, every deploy hangs at
  `WAITING_FOR_DOMAIN`.
- **Web UI: every funcId a location can be created with** is served from the
  generator rather than copied into the frontend, so `proxyRecorder` can be
  selected at last — the hardcoded list omitted it.
- **`ui --host`** — bind the web UI to something other than loopback, for when
  the machine running it is not the machine you are sitting at. The default is
  unchanged (`127.0.0.1`) and a widened bind warns at startup: the server holds
  your API key in process memory, and downloading a bundle rotates the
  AUTH_TOKEN out from under any agent already running for that ship. An SSH
  tunnel to the default bind does the same job without exposing the listener.

### Fixed

- **`IMAGE_OVERRIDES` came out empty for a location with no live agent.** The
  built-in image catalogue held only the two performance images, so a
  `mockServices` or `proxyRecorder` location generated overrides that covered
  nothing — and crane resolves a missing key against the *public* registry
  silently, so the bundle looks correct right up until the customer's cluster is
  actually sealed. The catalogue now covers mock, recorder and doduo. GUI browser
  images remain uncoverable without a running agent (60+ version-pinned repos,
  and only the agent says which one a location uses); that is flagged, and
  escalated to a warning when a private registry is set.
- **A Kubernetes agent's image inventory was being discarded.** k8s agents report
  bare keys (`taurus-cloud:latest`) where Docker agents report registry-qualified
  tags, and only the Docker shape was handled — so every k8s agent, which is the
  kind this tool generates for, silently produced no inventory and fell back to
  the catalogue. Reading it properly also pins exact tags where the catalogue
  could only say `latest`.

### Changed

- **The README is now short**, covering what the tool is, how to install it and
  how to get a bundle out. The reference material it used to carry — every
  option, the web UI, Helm, service virtualization, preflight, the live rig — is
  in [`docs/`](https://github.com/benjithompson/bzm-opl-generator/blob/main/docs/), linked from a table in the README.

## [0.1.0] — 2026-07-26

First packaged release.

### Added

- **`generate`** — renders private-location manifests for Kubernetes and
  OpenShift from a location's real account facts, so the features it actually
  has decide which images ship and what `IMAGE_OVERRIDES` a private registry
  needs. Scenario presets cover the standard, private-registry and proxy/CA
  postures.
- **`facts`** — reads a location's enabled features, agents and live image
  inventory straight from the account, instead of you transcribing them.
- **`doctor`** — preflights a target cluster before anyone waits on a stuck
  run: capacity, quota, LimitRange, admission (PSA/SCC), ingress class and
  egress. Exits non-zero on anything that would stop a test from starting.
- **`toolcheck`** — preflights your own machine against the live rig's
  requirements, so a missing tool fails in seconds rather than 15 minutes in.
- **`livetest`** — deploys the generated manifests and waits for the agent to
  report online. Optional rigs reproduce the awkward customer environments
  locally: air-gapped registry, proxy with a custom CA, default-deny egress,
  and a real engine run.
- **`images`** — lists, pulls and mirrors a location's images to a private
  registry.
- **Web UI** (`bzm-opl-gen ui`) — connect, pick or create a location,
  configure, preview the manifests live and download them as a zip. Ships
  prebuilt in the wheel; no Node toolchain needed.
- **Service virtualization** — ingress configuration for istio, contour, nginx
  and OpenShift routes, plus `sv-expose` for reaching a virtual service where
  crane's own nginx Ingress doesn't resolve.

[Unreleased]: https://github.com/benjithompson/bzm-opl-generator/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/benjithompson/bzm-opl-generator/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/benjithompson/bzm-opl-generator/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/benjithompson/bzm-opl-generator/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/benjithompson/bzm-opl-generator/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/benjithompson/bzm-opl-generator/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/benjithompson/bzm-opl-generator/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/benjithompson/bzm-opl-generator/releases/tag/v0.1.0
