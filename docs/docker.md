# Docker output format

`--format docker` is the other platform, not another way of writing the same
one. A private location on Docker is **one agent as one container** on a host
with a docker daemon; crane starts each engine as a sibling container on that
same host, through the socket it is given.

```
bzm-opl-gen generate --format docker --auth-token <AUTH_TOKEN> -o out/

# on the host that is to be the private location
./out/bzm-opl-agent.sh
```

The bundle is:

| file | |
|---|---|
| `bzm-opl-agent.sh` | the `docker run` command, with the settings folded in |
| `bzm-opl-agent.env` | the `AUTH_TOKEN`, when `use_secret` is on (the default) |
| `ca-bundle.crt` | the inline PEM, when one was given |
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

## Worth knowing

- **The socket is the point, and it is root.** Crane starts engines through
  `/var/run/docker.sock`; access to it is effectively root on the machine.
  BlazeMeter's own instructions say the same.
- **Size the host for the location, not for crane.** Every engine is another
  container on it. `bzm-opl-gen plan` sizes the whole thing.
- **One agent per host.** The container is named `bzm-crane-<shipId>`, as
  BlazeMeter names it, and the script refuses rather than replacing an existing
  one — that container may be the agent currently serving this location.
  `docker rm -f` it deliberately.
- **Docker Desktop for Mac 4.3.0+** additionally needs `--privileged -v
  /sys/fs/cgroup:/sys/fs/cgroup:rw`, per BlazeMeter's installation page. The
  generated script does not add them: they are a property of that one runtime,
  not of the bundle.

Two things differ from the Kubernetes formats, both deliberate:

- **Service virtualization is not supported.** A docker agent can serve virtual
  services, but it publishes them with `HOSTNAME_OVERRIDE` and a
  `TLS_CERT`/`TLS_KEY` pair, and every `sv_*` option here is a
  `KUBERNETES_WEB_EXPOSE_*` one. `--format docker` refuses a bundle *configured*
  for service virtualization — an `sv_ingress` other than none — rather than
  emitting a command that would install, report idle, and publish nothing. The
  test is the configuration, not the location: a location that offers mocks but
  is being generated for performance alone ([declining the
  functionality](service-virtualization.md#not-using-it-on-a-location-that-offers-it))
  carries no `sv_*` options and docker is available again.
- **`livetest` does not take a docker bundle.** The rig applies YAML to a
  cluster; this bundle is a shell script and no cluster is involved. It exits
  with that message rather than globbing an empty directory and waiting out its
  timeout.
