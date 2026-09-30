# Docker output format

`--format docker` targets a different platform. A private location on Docker is
**one agent as one container** on a host with a docker daemon; crane starts each
engine as a sibling container on that same host, through the docker socket.

```
bzm-opl-gen generate --format docker --auth-token <token> -o out/

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
| `README.md`, `IMAGES.md`, `profile.json` | as every format ([images.md](images.md)) |

## Where the command comes from

The shape is BlazeMeter's own — the **Docker Command** tab on an agent, and
`POST /private-locations/{harbor}/ships/{ship}/docker-command` — as described on
their [Docker installation
page](https://help.blazemeter.com/docs/guide/private-locations-install-blazemeter-agent-for-docker.html)
and [agent environment
variables](https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html)
reference, plus the bundle's own settings. It is built locally rather than
fetched, so a bundle can be produced for an account you cannot log in to.

Two things in BlazeMeter's generated command are not mentioned on those pages,
and the bundle carries both:

- **`-u 0`.** The crane image runs as a non-root user and
  `/var/run/docker.sock` is `root:docker 0660` on a stock daemon; without it the
  container dies with `PermissionError(13, 'Permission denied')` from
  `docker/transport/unixconn`.
- **`DOCKER_PORT_RANGE`.** `--net=host` makes an engine's ports the host's
  ports. This bundle uses `6000-7000` — 1000 host ports that must be free on
  the host and reachable by anything the engines serve.

## Most options mean nothing here

There is no namespace, no ServiceAccount, no toleration, no pod. Nearly thirty
options are Kubernetes vocabulary, and a docker agent has nowhere to put them.
`run_as_user` is ignored because the answer is fixed: the container runs as root
(`-u 0`) to open the docker socket.

Ignored options are **named rather than refused**: the bundle's README lists,
under **Set here, but not carried**, the ones you set away from their default.
The web UI hides them for this format. A format never rejects a value it
ignores, so an empty service account or a second CA mode does not stop a docker
bundle.

These options do reach it:

- **`auth_token`, `private_registry`** — identity and where engine images come
  from (`DOCKER_REGISTRY`). `registry_auth` does not: a docker host
  authenticates a pull with its own `docker login`.
- **`proxy`** — `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`, credentials embedded
  in the URL. `NO_PROXY` defaults to `127.0.0.1,localhost`, which BlazeMeter's
  proxy page requires for transaction-based virtual services.
- **`ca_bundle`** — written beside the script and mounted at
  `/etc/ssl/certs/ca-certificates.crt`, where `REQUESTS_CA_BUNDLE` and
  `AWS_CA_BUNDLE` point. It **replaces** the container's CA store, so it must be
  a full bundle — your CA and the public roots — or the agent stops trusting
  BlazeMeter. `ca_bundle_slot` with `ca_cert_file` mounts a file you supply
  the same way; the ConfigMap modes do not apply.
- **`extra_env`** — as `--env NAME=value` flags. Names the generator writes in
  any format, including `KUBERNETES_*`, are refused. See
  [Options](options.md#agent-environment).
- **`use_secret`** — see below.
- **`auto_update`** — `AUTO_UPDATE`, the Docker variable (not
  `AUTO_KUBERNETES_UPDATE`). Left unset unless you answer it: there is no
  Deployment for a self-update to conflict with. See [Helm](helm.md).

Service virtualization has its own three options on this format; see
[below](#service-virtualization).

## `use_secret` is `--env-file`

The credential lives apart from the configuration, in `bzm-opl-agent.env`. A
value passed with `--env` is visible in the host's process list and in the shell
history of whoever ran it. With `use_secret` off you get BlazeMeter's own shape,
`--env AUTH_TOKEN=...` inline. A proxy URL carrying `user:password` follows the
same rule.

## Docker Compose

`compose.yaml` sits beside the script and describes the same container, for
customers who install with compose:

```
cd out/
docker compose up -d            # needs `docker compose version` 2 or newer
docker compose logs -f crane
```

It has no `version:` key, and states the project `name:` rather than taking it
from the directory the bundle was unzipped into. BlazeMeter publishes no compose
file, so it is generated to match the script exactly — same image, environment,
mounts, user, network mode, restart policy, working directory and command.

**Use one or the other.** Both name the container `bzm-crane-<shipId>`, so the
second to start fails:

```
Error response from daemon: Conflict. The container name
"/bzm-crane-<shipId>" is already in use by container "482fff816b3c..."
```

Two cranes on one agent identity would otherwise make BlazeMeter report
**duplicated results rather than an error**.

**Never rename the env file to `.env`.** Compose auto-loads `.env` for variable
interpolation *into `compose.yaml`*, not into the container, so a token there
would never reach crane. For the same reason every inline value is written with
`$` doubled (`a$b` as `a$$b`). The one deliberate interpolation is
`${CA_BUNDLE:-./ca-bundle.crt}`, matching the script's overridable `CA_BUNDLE`
for a host that already keeps a trust bundle.

A field left blank is refused by compose itself, naming the variable and file.

## Service virtualization

A docker agent publishes virtual services with `HOSTNAME_OVERRIDE` and a
`TLS_CERT`/`TLS_KEY` pair: `--sv-hostname`, `--sv-tls-cert` and `--sv-tls-key`.
The two PEMs are written into the bundle and mounted like `ca-bundle.crt`. See
[Service virtualization](service-virtualization.md#docker-a-hostname-and-a-certificate)
for the checks made at generate time. The four Kubernetes `sv_*` options are
ignored here.

## Worth knowing

- **The socket is root.** Crane starts engines through `/var/run/docker.sock`;
  access to it is effectively root on the machine, as BlazeMeter's own
  instructions say.
- **Size the host for the location, not for crane.** Every engine is another
  container on it. `bzm-opl-gen plan` sizes the whole thing.
- **One agent per host.** Neither route replaces an existing
  `bzm-crane-<shipId>` container — it may be the agent currently serving this
  location. `docker rm -f` it deliberately.
- **Crane does not pull here.** It asks the daemon to *create* each container,
  so an image the host does not already hold ends the deploy `FAILED` about
  ninety seconds later, with no message mentioning a pull — for engines and mock
  services alike. A bundle generated with `--private-registry` lists the exact
  `docker pull` commands in its README (the `:latest` forms under
  `DOCKER_REGISTRY`, not the tags the location pins); see
  [Service virtualization](service-virtualization.md#what-a-live-run-showed).
  Without a registry, the README names the image keys and says the prefix is
  crane's default rather than guessing it.
- **`--private-registry` mirrors to the name crane composes.** There is no
  `IMAGE_OVERRIDES` on docker, so the mirror script pushes each image to
  `<registry>/<key>:latest`. Crane's own image is named directly by the script
  and compose file.
- **Docker Desktop for Mac 4.3.0+** additionally needs `--privileged -v
  /sys/fs/cgroup:/sys/fs/cgroup:rw`, per BlazeMeter's installation page. The
  script does not add them; they are a property of that runtime.
- **`livetest` takes a docker bundle** through the compose file: it runs
  `docker compose up -d`, waits for the agent to report online, and takes it
  down. The cluster-only flags are refused. It never starts an engine — see
  [Live test](live-test.md#what-the-compose-path-does-not-prove).
