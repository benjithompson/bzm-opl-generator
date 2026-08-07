# Docker output format

`--format docker` is the other platform, not another way of writing the same
one. A private location on Docker is **one agent as one container** on a host
with a docker daemon; crane starts each engine as a sibling container on that
same host, through the socket it is given.

```
bzm-opl-gen generate --format docker --auth-token <AUTH_TOKEN> -o out/

# on the host that is to be the private location
./out/bzm-opl-agent.sh          # ...or `docker compose up -d` in out/
```

The bundle is:

| file | |
|---|---|
| `bzm-opl-agent.sh` | the `docker run` command, with the settings folded in |
| `compose.yaml` | the same container for Docker Compose — see below |
| `bzm-opl-agent.env` | the `AUTH_TOKEN`, when `use_secret` is on (the default) |
| `ca-bundle.crt` | the inline PEM, when one was given |
| `sv-tls.crt`, `sv-tls.key` | the certificate this agent serves its virtual services with, when one was given |
| `bzm-opl-image-mirror.sh` | when `--private-registry` was given |
| `README.md`, `profile.json` | as every format |

## Where the command comes from

BlazeMeter generates this command themselves — the **Docker Command** tab on an
agent, and `POST /private-locations/{harbor}/ships/{ship}/docker-command`, which
this repo already calls to mint a token. The shape here is theirs, from their
[Docker installation
page](https://help.blazemeter.com/docs/guide/private-locations-install-blazemeter-agent-for-docker.html)
and their [agent environment
variables](https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html)
reference; what this adds is the bundle's own settings, which their generated
command cannot know.

It is **built** rather than fetched, for the same reason every other format is:
`generate` reaches nothing, so a bundle can be produced for an account nobody
here can log in to — which is what `facts.manual()` exists for.

That has a cost, and it has been paid once: **their generated command carries
two things their documentation does not mention**, and building from the docs
alone missed both.

- **`-u 0`.** The crane image runs as a non-root user and `/var/run/docker.sock`
  is `root:docker 0660` on a stock daemon, so the container started, reached the
  socket and died with `PermissionError(13, 'Permission denied')` out of
  `docker/transport/unixconn` — a traceback naming neither the uid that could
  not open it nor the flag that would have. Starting engines through that socket
  is the only thing the agent does.
- **`DOCKER_PORT_RANGE`.** `--net=host` makes an engine's ports the host's
  ports, and their command always names the range. This bundle emits
  `6000-7000` — 1000 host ports, which must be free on the host and reachable
  by anything the engines serve.

So when checking this format against BlazeMeter, check it against the **command
their API returns**, not against the pages describing it.

## Most options mean nothing here

There is no namespace, no ServiceAccount, no toleration, no pod. Two dozen of
this generator's options are Kubernetes vocabulary, and a docker agent has
nowhere to put them.

`run_as_user` is the one to read twice: it is ignored *because the answer is
fixed*, not because the container has no user. It runs as root (`-u 0`), which
is what opens the docker socket.

They are **named rather than refused**, per bundle: the README lists only the
ones set away from their default, so it says what *this* bundle asked for and
did not get. A note that listed all of them every time would be read as
boilerplate, and the one line that matters — "you asked for a node selector and
it is not here" — would be buried in it.

The table is the generator's own (`generate.IGNORED_BY_FORMAT`) and is **served
as `/api/ignored-options`**, keyed by format, so the web UI hides what this
format cannot carry without keeping a second copy of two dozen option names —
a key added to the generator stops being offered there with no edit on either
side. It is a table per format rather than docker's alone: `manifests` and
`helm` ignore the three options a docker agent publishes virtual services with,
which is the same rule read from the other end.

Named rather than refused cuts the other way too, and the generator holds to it:
**a format never rejects a value it says it ignores.** An unnamed service
account, a malformed engine limit or a second CA mode all refuse a Kubernetes
bundle and none of them refuses this one — the web UI hides those fields here,
so a refusal would be a block with nothing on screen to clear it.

Seven options do reach it:

- **`auth_token`, `private_registry`** — identity and where engine images come
  from (`DOCKER_REGISTRY`). `registry_auth` does not: its stubs are ConfigMap
  lines, and a docker host authenticates a pull with its own `docker login`.
- **`proxy`** — `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`, credentials embedded
  in the URL exactly as the Kubernetes path does. `NO_PROXY` defaults to
  `127.0.0.1,localhost` here rather than the cluster default, which names
  `kubernetes.default` — the API service, and nothing a docker host resolves.
  Those two entries are required by BlazeMeter's proxy page or transaction-based
  virtual services break against their own local calls.
- **`ca_bundle`** — written beside the script and mounted at
  `/etc/ssl/certs/ca-certificates.crt`, where `REQUESTS_CA_BUNDLE` and
  `AWS_CA_BUNDLE` point. It **replaces** the container's CA store rather than
  adding to it, so it must be a full bundle — your CA and the public roots — or
  the agent stops trusting BlazeMeter. The other two CA modes name a ConfigMap
  and have nothing to read here.
- **`extra_env`** — the free-form agent variables, as `--env NAME=value` flags
  on the command. Not Kubernetes vocabulary, so it is not in the ignored table
  and the web UI keeps the area for this format. The reserved names it refuses
  are the union across formats, though, so a `KUBERNETES_*` variable is refused
  here too: it would reach nothing on a docker host either, and accepting it
  would read as a setting that had been made. See
  [Options](options.md#agent-environment).
- **`use_secret`** — see below.
- **`auto_update`** — `AUTO_UPDATE`, which is the Docker variable;
  `AUTO_KUBERNETES_UPDATE` is a different one and inert here. Left unset unless
  it was answered, unlike the Kubernetes path which forces it off: what it does
  here is pull a newer crane image for a container the operator started, and
  there is no Deployment for it to fight over — the specific hazard the
  Kubernetes default departs from BlazeMeter for. See [Helm](helm.md).

## `use_secret` is `--env-file`

It means what it means for Kubernetes — the credential lives apart from the
configuration — and docker's mechanism for it is `--env-file`. The difference is
not cosmetic: a value passed with `--env` is in the host's process list for
anyone running `ps`, and in the shell history of whoever ran it.

With it off you get BlazeMeter's own shape, `--env AUTH_TOKEN=...` inline and no
second file. A proxy URL carrying `user:password` moves into the env file too,
by the same rule the Kubernetes Secret follows.

## Docker Compose

`compose.yaml` sits beside the script and describes the same container. It is
**not** a fourth `--format`: a format is a platform, and these are two syntaxes
for one. It buys no capability either — for a single container compose adds
nothing `docker run` cannot do — and it is here because some customers install
with compose and will not take a script.

```
cd out/
docker compose up -d            # needs `docker compose version` 2 or newer
docker compose logs -f crane
```

The name is `compose.yaml` because that is what compose looks for: the customer
unzips and runs `docker compose up -d` with no `-f`. There is no `version:` key
(obsolete since v2, and warned about), and the top-level `name:` is stated
rather than left to compose, which otherwise takes the project name from
whatever directory the bundle was unzipped into.

BlazeMeter publishes no compose file — their install page and their
`docker-command` endpoint both return a `docker run` — so the rule above, *check
it against the command their API returns*, has no counterpart here. What holds
it honest instead is parity with the script beside it: `user`, `network_mode`,
`restart`, `volumes`, `working_dir` and `command` are the generator's own
constants, read by both renderers, and the environment comes from
`docker_split_env()` for both.

### Two checks, and neither covers the other

A constant both renderers read makes the comparison cheap; it is not what
performs it, and a value written into one file alone would have no constant to
be caught by. So the bundle's two files are checked twice, and the questions are
different:

| check | where | what it answers |
|---|---|---|
| **parity** | `tests/test_generate.py::test_compose_and_docker_run_describe_the_same_container` | do the two files describe the *same container*? |
| **validity** | the `docker` job in `.github/workflows/tests.yml` | is `compose.yaml` a file *compose will accept*? |

Parity parses `compose.yaml` and the generated `bzm-opl-agent.sh` and holds them
against each other — same image, same environment by name and by value, same
mounts, same user, network mode, restart policy, working directory and command —
over `helm_parity.py`'s own option matrix plus every branch this format has of
its own. It is pytest rather than a script beside `helm_parity.py` because both
sides are built in Python from one call to `generate()`: there is no binary to
be missing, so nothing can skip. Two differences are representation rather than
substance and are undone before comparing: compose doubles every `$` in its own
values, and the split credential is in neither file's inline set. The one
licensed difference is a value nobody supplied — the marker to the script, which
greps for it, and `${...:?}` to compose, which has no shell to check anything in
— and that is asserted in both directions rather than skipped.

Validity is `docker compose config -q` over generated bundles, in CI. Parity
cannot answer it: two python dicts can agree perfectly about a document compose
refuses to parse. The job runs it over the default shape, over the branches the
default does not render (token inline, CA mount, private registry), and once in
the negative — a bundle with a field left blank has to be *refused*, naming the
variable and the file, or compose would start an agent the script beside it
refuses.

### Either/or, and docker enforces it

Both files start one agent for one `ship_id`, and running both would put two
cranes on one agent identity — which BlazeMeter reports as **duplicated results
rather than as an error**. So both name the container `bzm-crane-<shipId>` and
the second one to start refuses:

```
Error response from daemon: Conflict. The container name
"/bzm-crane-<shipId>" is already in use by container "482fff816b3c..."
```

A README warning fails at "why are my results duplicated"; a name collision
fails at `compose up`.

### Never a file called `.env`

`use_secret` writes the credential to `bzm-opl-agent.env`, and compose reads
that same file through `env_file:`. The name matters: compose auto-loads a file
called `.env` for **variable interpolation into `compose.yaml`**, not into the
container. An `AUTH_TOKEN` moved there would never reach crane while looking
exactly as though it had, and a `$` in a proxy password would be substituted on
the way past. The compose file carries that as a comment, because renaming it is
the tidy-up somebody will reach for.

The same interpolation is why every value the compose file carries inline is
written with `$` doubled — `a$b` is emitted as `a$$b`, which arrives in the
container as `a$b`. Escaped rather than moved into the env file: which variables
live there is `use_secret`'s answer, and a value that changed file depending on
its punctuation is a bundle nobody could reason about. The one deliberate
interpolation is the CA mount, `${CA_BUNDLE:-./ca-bundle.crt}`, which is the
counterpart of the script's overridable `CA_BUNDLE` — a host may already keep
the trust bundle its platform team maintains.

## Worth knowing

- **The socket is the point, and it is root.** Crane starts engines through
  `/var/run/docker.sock`; access to it is effectively root on the machine.
  BlazeMeter's own instructions say the same.
- **Size the host for the location, not for crane.** Every engine is another
  container on it. `bzm-opl-gen plan` sizes the whole thing.
- **One agent per host.** The container is named `bzm-crane-<shipId>`, as
  BlazeMeter names it, and neither route replaces an existing one — that
  container may be the agent currently serving this location. `docker rm -f` it
  deliberately.
- **Crane does not pull here.** It composes the image name and asks the daemon
  to *create* the container, so an image the host does not already hold ends the
  deploy `FAILED` about ninety seconds later, with no message on either side
  mentioning a pull. A bundle for a location that serves virtual services names
  the exact `docker pull` commands in its README — the `:latest` forms under
  `DOCKER_REGISTRY`, which are not the tags the location pins. What was seen
  live, and why the tag differs, is in
  [Service virtualization](service-virtualization.md#what-a-live-run-showed).
  Only the mock images have been measured; a performance bundle says nothing
  about pre-pulling because nobody has tested that half.
- **Docker Desktop for Mac 4.3.0+** additionally needs `--privileged -v
  /sys/fs/cgroup:/sys/fs/cgroup:rw`, per BlazeMeter's installation page. The
  generated script does not add them: they are a property of that one runtime,
  not of the bundle.

Two things differ from the Kubernetes formats, both deliberate:

- **Service virtualization is published a different way here, not left out.**
  This bundle used to refuse an SV configuration outright, and the refusal was
  always narrower than it read: a docker agent serves virtual services perfectly
  well, and the gap was in this generator rather than in the agent. It is closed
  ([#182](https://github.com/benjithompson/bzm-opl-generator/issues/182)) —
  `--sv-hostname`, `--sv-tls-cert` and `--sv-tls-key` write `HOSTNAME_OVERRIDE`,
  `TLS_CERT` and `TLS_KEY`, and the two PEMs are written into the bundle and
  mounted the way `ca-bundle.crt` is. The full shape, and the two checks that
  run when the bundle is generated, are in
  [Service virtualization](service-virtualization.md#docker-a-hostname-and-a-certificate).

  The four Kubernetes `sv_*` options are ignored here rather than refused: they
  write `KUBERNETES_WEB_EXPOSE_*`, which a container agent never reads. Set one
  and the bundle names it under **Set here, but not carried**, like every other
  ignored option. `--format helm` is now the only format that refuses a virtual
  service, and that is a limit of *our chart*.
- **`livetest` takes a docker bundle, through the compose file.** It used to
  refuse one outright — the rig applies YAML to a cluster, and this bundle is a
  container on a host — which left `--format docker` the one format never live
  tested at all. It now brings the bundle up with `docker compose up -d`, waits
  for the agent to report online in the account, and takes it down again: no
  namespace, no cluster, no `--local-registry`/`--local-proxy`/`--contain-egress`
  /`--run-test` (each of those is cluster-shaped and is refused rather than
  ignored). It never starts an engine, so `-u 0` and `DOCKER_PORT_RANGE` are
  still unproven there — see [Live test](live-test.md#what-the-compose-path-does-not-prove).
