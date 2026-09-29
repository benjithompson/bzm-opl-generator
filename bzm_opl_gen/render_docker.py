"""The docker bundle: one agent as one container on a host with a docker
daemon.

The shape is BlazeMeter's own Docker Command; check it against the command
their API returns, since `-u 0` and DOCKER_PORT_RANGE are in it and in none of
their pages. The bundle carries the container twice, as a run script and a
compose file, and the two are either/or, enforced by the container name they
share. Most options are Kubernetes vocabulary and are named in the README
rather than refused.
"""

import collections

from .bundle_env import extra_env, proxy_env, proxy_has_creds
from .bundle_names import (DOCKER_CA_FILE, DOCKER_COMPOSE_FILE,
                           DOCKER_COMPOSE_SERVICE, DOCKER_ENV_FILE,
                           DOCKER_RUN_FILE, DOCKER_SV_CERT_FILE,
                           DOCKER_SV_KEY_FILE, MIRROR_SCRIPT_FILE,
                           docker_container_name)
from .ca_trust import ca_cfg
from .facts import key_base, select_images
from .image_registry import (crane_image, docker_composed_targets,
                             mirror_script)
from .markers import MARKER_PATTERN, marker_in
from .quoting import compose_value, sh_value
from .readme_parts import (bundle_table, ca_slot_block, ignored_block,
                           location_bullet, placeholder_block,
                           sizing_bullet, sizing_vocab)
from .service_virt import sv_docker_cfg


# Prefix of the variable a blank value's compose guard reads: one no host sets
# (see _compose_required). livetest reads it back.
COMPOSE_UNSET_PREFIX = "BZM_OPL_UNSET_"

# The container's CA store, where BlazeMeter's docs put the trust bundle; the
# mounted file replaces it.
DOCKER_CA_PATH = "/etc/ssl/certs/ca-certificates.crt"

# Where TLS_CERT and TLS_KEY point: the paths in BlazeMeter's
# bring-your-own-certificate command.
DOCKER_SV_CERT_PATH = "/etc/ssl/certs/public.pem"
DOCKER_SV_KEY_PATH = "/etc/ssl/certs/privatekey.pem"

# BlazeMeter's proxy docs require 127.0.0.1 and localhost, or transaction-based
# virtual services break on their own local calls.
DOCKER_NO_PROXY = "127.0.0.1,localhost"

# The fixed part of BlazeMeter's command. Crane starts engines through the
# docker socket; /tmp is shared so engines can hand back artifacts.
DOCKER_MOUNTS = ["/var/run/docker.sock:/var/run/docker.sock", "/tmp:/tmp"]

# Host ports crane gives its engines (under --net=host), as BlazeMeter's
# generated command sets them.
DOCKER_PORT_RANGE = "6000-7000"
DOCKER_WORKDIR = "/usr/src/app/"
DOCKER_ENTRYPOINT = "python agent/agent.py"

# Root: the socket is root:docker 0660 on a stock host and the crane image runs
# as non-root, which dies on `PermissionError: [Errno 13]`. BlazeMeter's
# generated command carries `-u 0`.
DOCKER_USER = "0"

# As BlazeMeter's command: a crane that exits cleanly was told to stop.
DOCKER_RESTART = "on-failure"

# Host networking, so the agent advertises an address its engines can reach.
DOCKER_NETWORK = "host"


def docker_env(facts, o):
    """Every variable the container needs, in order, as {NAME: value}.
    use_secret decides which file each is written to (docker_split_env).
    """
    env = {
        "HARBOR_ID": facts["harbor_id"],
        "SHIP_ID": o["ship_id"],
        "AUTH_TOKEN": o["auth_token"],
        "CONTAINER_MANAGER_TYPE": "DOCKER",
        "DOCKER_PORT_RANGE": DOCKER_PORT_RANGE,
    }
    # Only with a private registry, as BlazeMeter's command does. Crane
    # composes `<DOCKER_REGISTRY>/<key>:latest` and the keys are not uniform
    # (`blazemeter/service-mock`, `taurus-cloud`), so pointing it at the public
    # mirror asks for tags that do not exist.
    if o["private_registry"]:
        env["DOCKER_REGISTRY"] = o["private_registry"]
    # Off unless asked. Started from the pinned mirror reference, crane's
    # updater removes and retags images on the host and loops on `Failed to
    # reload crane`: tests sit at BOOT_STARTING, and other agents on the host
    # break (measured). BlazeMeter's command leaves it unset because it runs
    # the Docker Hub name.
    env["AUTO_UPDATE"] = "true" if o["auto_update"] else "false"
    sv = sv_docker_cfg(o)
    if sv:
        if sv["hostname"]:
            env["HOSTNAME_OVERRIDE"] = sv["hostname"]
        # Only with a certificate: a TLS_CERT pointing at nothing stops the
        # listener.
        if sv["cert"]:
            env["TLS_CERT"] = DOCKER_SV_CERT_PATH
            env["TLS_KEY"] = DOCKER_SV_KEY_PATH
    env.update(proxy_env(o, no_proxy=DOCKER_NO_PROXY))
    # Both, per BlazeMeter's CA page: crane's HTTP client reads the first, boto
    # the second.
    if ca_cfg(o):
        env["REQUESTS_CA_BUNDLE"] = DOCKER_CA_PATH
        env["AWS_CA_BUNDLE"] = DOCKER_CA_PATH
    # Last, and with the command rather than the env file: it is configuration,
    # not a credential.
    env.update(extra_env(o))
    return env


def docker_split_env(facts, o):
    """The environment split into (command, env_file).

    With use_secret the credential, and proxy URLs carrying credentials, go to
    --env-file, out of `ps` and shell history. Without it everything is inline,
    like BlazeMeter's own command.
    """
    env = docker_env(facts, o)
    if not o["use_secret"]:
        return env, {}
    secret = {"AUTH_TOKEN": env.pop("AUTH_TOKEN")}
    if proxy_has_creds(o):
        for name in ("HTTP_PROXY", "HTTPS_PROXY"):
            if name in env:
                secret[name] = env.pop(name)
    return env, secret


# One mounted file: its override variable, bundle file, container path,
# description, source option and content.
DockerMount = collections.namedtuple(
    "DockerMount", "var file path what option content")

# Every file a docker bundle can mount, without content, for readers that need
# only the names (livetest: profile.json has no sv_tls_key).
DOCKER_FILE_MOUNTS = (
    DockerMount("CA_BUNDLE", DOCKER_CA_FILE, DOCKER_CA_PATH, "trust bundle",
                "ca_bundle", None),
    DockerMount("SV_TLS_CERT", DOCKER_SV_CERT_FILE, DOCKER_SV_CERT_PATH,
                "virtual-service certificate", "sv_tls_cert", None),
    DockerMount("SV_TLS_KEY", DOCKER_SV_KEY_FILE, DOCKER_SV_KEY_PATH,
                "virtual-service private key", "sv_tls_key", None),
)

_MOUNT_BY_OPTION = {m.option: m for m in DOCKER_FILE_MOUNTS}


def docker_file_mounts(o):
    """The files this bundle writes and mounts.

    The script's override variables and existence checks, its -v lines,
    compose's binds and the files themselves all walk this list. Each is
    overridable to a file the host already keeps.
    """
    def mount(option, content):
        return _MOUNT_BY_OPTION[option]._replace(content=content)

    out = []
    if ca_cfg(o):
        out.append(mount("ca_bundle", o["ca_bundle"]))
    sv = sv_docker_cfg(o)
    if sv and sv["cert"]:
        out += [mount("sv_tls_cert", sv["cert"]), mount("sv_tls_key", sv["key"])]
    return out


def _docker_run_lines(facts, o):
    """The `docker run` invocation, one argument per line, from the constants
    compose reads too.
    """
    cmd, secret = docker_split_env(facts, o)
    lines = ["docker run -d \\",
             '  --name "$NAME" \\',
             f"  --restart {DOCKER_RESTART} \\",
             f"  -u {DOCKER_USER} \\"]
    if secret:
        lines.append('  --env-file "$ENV_FILE" \\')
    lines += [f"  --env {k}={sh_value(v)} \\" for k, v in cmd.items()]
    lines += [f"  -v {m} \\" for m in DOCKER_MOUNTS]
    lines += [f'  -v "${m.var}":{m.path}:ro \\'
              for m in docker_file_mounts(o)]
    lines += [f"  -w {DOCKER_WORKDIR} \\",
              f"  --net={DOCKER_NETWORK} \\",
              f"  {crane_image(facts, o)} {DOCKER_ENTRYPOINT}"]
    return "\n".join(lines)


def _docker_where(in_env_file):
    """The file a value is filled in in: the env file, or both launch files for
    an inline value.
    """
    return (DOCKER_ENV_FILE if in_env_file
            else f"{DOCKER_RUN_FILE} and {DOCKER_COMPOSE_FILE}")


# A variable rendered blank: its name, the file it is set in, and the marker
# found in it.
BlankEnv = collections.namedtuple("BlankEnv", "name where marker")


def _docker_blank_env(facts, o):
    """Every variable this bundle renders with a marker in it.

    Read off rendered values, since a value can contain a marker (a proxy URL
    built from a blank host). docker_env writes no ignored option, so nothing
    here is off-screen. A marked image reference is left to docker, which
    refuses it as an invalid reference.
    """
    cmd, secret = docker_split_env(facts, o)
    found = []
    for env, in_file in ((cmd, False), (secret, True)):
        for k, v in env.items():
            mark = marker_in(v)
            if mark:
                found.append(BlankEnv(k, _docker_where(in_file), mark))
    return found


def _blank_env_by_name(facts, o):
    """_docker_blank_env keyed by variable name."""
    return {b.name: b for b in _docker_blank_env(facts, o)}


def _docker_blank_lines(name, where, mark):
    """What both routes say about a blank variable: what is wrong, then what to
    do.
    """
    return (f"{name} carries {mark} -- a required value was left blank "
            f"when this bundle was generated.",
            f"Set it in {where}, or re-generate the bundle with it filled in.")


def _compose_required(name, where, mark):
    """A blank value as compose's `${X:?message}`, which aborts `up` before
    anything is created.

    The variable is one no host sets: `${AUTH_TOKEN:?}` would read the ambient
    environment and pass on the host most likely to have one. It also works
    inside the env file, which is where the credential is guarded: a guard in
    compose.yaml would outlive the fix.
    """
    return "${%s%s:?%s}" % (COMPOSE_UNSET_PREFIX, name,
                            " ".join(_docker_blank_lines(name, where, mark)))


def _docker_blank_file_lines(m):
    """What both routes say about a mounted file left blank.

    The script reads the file and compose cannot, so replacing the file clears
    one route; setting the variable clears both.
    """
    return (f"{m.file} carries {marker_in(m.content)} -- the {m.what} was left "
            f"blank when this bundle was generated.",
            f"Set {m.var} to a {m.what} this host already has, or re-generate "
            f"the bundle with {m.option} filled in. Replacing {m.file} in place "
            f"clears {DOCKER_RUN_FILE} and not {DOCKER_COMPOSE_FILE}, which has "
            f"no way to read a file -- set {m.var} and both routes agree.")


def _compose_required_file(m):
    """A blank mounted file as compose's `${VAR:?message}` bind source.

    Unlike _compose_required this uses the bundle's own variable (SV_TLS_KEY,
    CA_BUNDLE): no host sets those by accident, and setting one is the
    documented fix.
    """
    return "${%s:?%s}" % (m.var, " ".join(_docker_blank_file_lines(m)))


def _docker_run_sh(facts, o):
    """bzm-opl-agent.sh: the checks, then `docker run`."""
    # Quoted in the script: an unquoted `bzm-crane-<SHIP_ID>` is a redirection,
    # a syntax error before any check runs.
    name = docker_container_name(o["ship_id"])
    # Siblings resolve against the script, not the working directory, and stay
    # overridable.
    mounts = docker_file_mounts(o)
    mount_lines = "".join(f'{m.var}="${{{m.var}:-$DIR/{m.file}}}"\n'
                          for m in mounts)
    mount_checks = ""
    for m in mounts:
        mount_checks += f'''
if [ ! -f "${m.var}" ]; then
  echo "{m.what} not found: ${m.var}" >&2
  echo "set {m.var}=/path/to/your/{m.file}, or put it beside this script" >&2
  exit 1
fi
'''
        # The existence check passes a file whose content is the marker, so the
        # resolved file's content is checked too.
        if marker_in(m.content):
            wrong, todo = _docker_blank_file_lines(m)
            mount_checks += f'''
if grep -q '{MARKER_PATTERN}' "${m.var}"; then
  echo "{wrong}" >&2
  echo "{todo}" >&2
  exit 1
fi
'''
    env_check = (f'''
if [ ! -f "$ENV_FILE" ]; then
  echo "{DOCKER_ENV_FILE} not found beside this script -- it holds the AUTH_TOKEN" >&2
  exit 1
fi
''') if o["use_secret"] else ""
    env_line = f'ENV_FILE="$DIR/{DOCKER_ENV_FILE}"\n' if o["use_secret"] else ""
    # One refusal per blank variable, over the files as they stand, so filling
    # a value in is the whole fix. Each grep is anchored to the line carrying
    # the value, so the script never matches its own message.
    blank_check = ""
    for b in _docker_blank_env(facts, o):
        target, anchor = (('"$ENV_FILE"', f"^{b.name}=")
                          if b.where == DOCKER_ENV_FILE
                          else ('"$0"', f"^  --env {b.name}="))
        wrong, todo = _docker_blank_lines(b.name, b.where, b.marker)
        blank_check += f'''
if grep -q '{anchor}.*{MARKER_PATTERN}' {target}; then
  echo "{wrong}" >&2
  echo "{todo}" >&2
  exit 1
fi
'''
    return f'''#!/bin/sh
# Starts the BlazeMeter agent for private location {facts.get("harbor_name") or facts["harbor_id"]}
# as one container on this host. See README.md.
#
# Needs docker and access to its socket, which crane uses to start engines.
# Use this script or compose.yaml, never both.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
NAME="{name}"
{env_line}{mount_lines}
if docker ps -a --format '{{{{.Names}}}}' | grep -qx "$NAME"; then
  # Not removed automatically: it may be the agent currently serving this location.
  echo "$NAME already exists." >&2
  echo "Remove it first if it is the old agent: docker rm -f $NAME" >&2
  exit 1
fi
{env_check}{mount_checks}{blank_check}
{_docker_run_lines(facts, o)}

echo "started $NAME -- follow it with: docker logs -f $NAME"
'''


def _docker_env_file(facts, o):
    """bzm-opl-agent.env, the credential half, for --env-file: NAME=value, no
    quoting.

    A blank value is written as compose's guard, which docker does not expand;
    the script refuses the marker inside it first.
    """
    _, secret = docker_split_env(facts, o)
    if not secret:
        return None
    blank = _blank_env_by_name(facts, o)
    lines = "".join(
        f"{k}={_compose_required(k, blank[k].where, blank[k].marker)}\n"
        if k in blank else f"{k}={v}\n"
        for k, v in secret.items())
    note = ("# A `${...:?}` line below is a value left blank; replace the whole line.\n"
            if blank else "")
    return ("# Read by docker --env-file: NAME=value, no quotes, no export.\n"
            "# Holds the agent's token; keep it private (chmod 600).\n"
            + note + lines)


def _docker_compose_yaml(facts, o):
    """compose.yaml: the same container as the script.

    It adds no capability; it is there because some customers install only with
    compose. The shared container_name makes running both fail at `compose up`.
    """
    cmd, secret = docker_split_env(facts, o)
    name = docker_container_name(o["ship_id"])
    body = [
        # No `version:` key (obsolete in Compose v2). The project is named
        # after the agent, not the directory it was unzipped into.
        f"name: {compose_value(name)}",
        "services:",
        f"  {DOCKER_COMPOSE_SERVICE}:",
        f"    image: {compose_value(crane_image(facts, o))}",
        f"    container_name: {compose_value(name)}",
        f"    user: {compose_value(DOCKER_USER)}",
        f"    restart: {DOCKER_RESTART}",
        f"    network_mode: {DOCKER_NETWORK}",
        f"    working_dir: {compose_value(DOCKER_WORKDIR)}",
    ]
    if secret:
        body += ["    env_file:", f"      - ./{DOCKER_ENV_FILE}"]
    body.append("    environment:")
    # A blank value is written as the guard, the only thing that refuses it on
    # this route.
    blank = _blank_env_by_name(facts, o)
    body += [
        f'      {k}: "{_compose_required(k, blank[k].where, blank[k].marker)}"'
        if k in blank else f"      {k}: {compose_value(v)}"
        for k, v in cmd.items()]
    body.append("    volumes:")
    body += [f"      - {m}" for m in DOCKER_MOUNTS]
    # Binds mirror the script's overridable variables; a blank file drops the
    # default so the guard fires.
    for m in docker_file_mounts(o):
        if marker_in(m.content):
            body.append(f'      - "{_compose_required_file(m)}:{m.path}:ro"')
        else:
            body.append(f"      - ${{{m.var}:-./{m.file}}}:{m.path}:ro")
    body.append(f"    command: {compose_value(DOCKER_ENTRYPOINT)}")
    dot_env = (f"""# Do not rename {DOCKER_ENV_FILE} to `.env`: compose would read it for
# substitution into this file, and the token would never reach the container.""") if secret else (
        """# Do not add a `.env` file here: compose reads it for substitution into this
# file, not for the container.""")
    where = facts.get("harbor_name") or facts["harbor_id"]
    head = f'''# The BlazeMeter agent for private location {where},
# as one container on this host. See README.md.
#
#   docker compose up -d
#
# Needs Docker Compose v2 and access to the docker socket, which crane uses to
# start engines. Use this file or {DOCKER_RUN_FILE}, never both.
#
{dot_env}
'''
    return head + "\n".join(body) + "\n"


def _docker_sv_block(facts, o):
    """How this bundle publishes virtual services and what was checked, or "".
    A certificate that could not be read is said to be unchecked.
    """
    sv = sv_docker_cfg(o)
    if not sv:
        return ""
    if not sv["hostname"]:
        return f"""
- **Virtual services are served over TLS at this host's IP address.**
  `{DOCKER_SV_CERT_FILE}` and `{DOCKER_SV_KEY_FILE}` are mounted at
  `{DOCKER_SV_CERT_PATH}` and `{DOCKER_SV_KEY_PATH}`. With no `sv_hostname`,
  endpoint URLs use this machine's IP address, so clients reject the
  certificate unless it covers that address.
"""
    lines = [f"""
- **Virtual services are published as `{sv["hostname"]}`** (`HOSTNAME_OVERRIDE`).
  Create a DNS record so that name resolves to this host for your clients."""]
    if sv["cert"]:
        names = sv["names"]
        if names is None:
            checked = (
                f"\n  **The hostname was not checked against the certificate.** "
                f"`{DOCKER_SV_CERT_FILE}` could not be read as an X509 "
                f"certificate, so check that it covers `{sv['hostname']}`: "
                f"`openssl x509 -in {DOCKER_SV_CERT_FILE} -noout "
                f"-text | grep -A1 'Subject Alternative Name'`.")
        else:
            checked = (
                f"\n  `{DOCKER_SV_CERT_FILE}` and `{DOCKER_SV_KEY_FILE}` are "
                f"mounted at `{DOCKER_SV_CERT_PATH}` and "
                f"`{DOCKER_SV_KEY_PATH}`. The certificate names "
                f"{', '.join(f'`{n}`' for n in names)}, which covers the "
                f"hostname. Its expiry, issuer and key pair were not "
                f"checked.")
        lines.append(checked)
    else:
        lines.append(
            "\n  No certificate was given, so the endpoints are plain HTTP. "
            "For HTTPS, set `sv_tls_cert` and `sv_tls_key` (PEM, key in "
            "PKCS#8).")
    return "".join(lines) + "\n"


def _docker_prepull_block(facts, o):
    """The images to pull onto the host before the first engine or virtual
    service, or "".

    Crane does not pull on docker: it asks the daemon to create the container,
    and a missing image fails after about ninety seconds with nothing naming a
    pull. The references are docker_composed_targets, which the mirror script
    pushes; `latest` is what crane asks for, not the pinned tag.
    """
    targets = docker_composed_targets(facts, o)
    # No registry: crane's default names apply, and none are guessed.
    if not targets:
        keys = ", ".join(f"`{key_base(i['key'])}`" for i in select_images(facts))
        return f"""
- **Images must already be on this host.** Crane does not pull on docker: a
  missing image makes the first engine or virtual service end `FAILED`
  (`Failed to find a deployed container` in BlazeMeter, `No such image` in
  `docker logs`). This location uses {keys}, from crane's default registry.
  Generate with a private registry to get the exact `docker pull` commands.
"""
    pulls = "\n".join(f"  docker pull {ref}" for ref in targets.values())
    return f"""
- **Pull these onto this host first.** Crane does not pull on docker: a
  missing image makes the first engine or virtual service end `FAILED`
  (`Failed to find a deployed container` in BlazeMeter, `No such image` in
  `docker logs`).

  ```
{pulls}
  ```

  Use these exact names, `latest` included: that is the tag crane asks for.
  On a host that cannot reach the registry, `docker save` them elsewhere and
  `docker load` them here.
"""


def _docker_socket_bullet(facts, o):
    """Why the socket is mounted, in the word for what this location starts
    through it (containers, never pods).
    """
    m = sizing_vocab(facts, o)
    starts = ("engines as containers on this host" if m is None or m["engine"]
              else "the containers this location needs on this host")
    return (f"- **Docker socket.** Crane starts {starts}\n"
            f"  through `/var/run/docker.sock`, which is effectively root access "
            f"to the machine.")


def _docker_beside_bullet(facts, o):
    """What the host is sized for: the location's containers, not crane."""
    m = sizing_vocab(facts, o)
    if m is None or m["engine"]:
        return ("- **Size the host for the engines**, not for crane: every engine "
                "is another\n  container here.")
    return (f"- **Size the host for this location's {m['runs']}**, not for crane: "
            f"each one is\n  another container here.")


def _docker_readme(facts, o):
    """README.md for the docker bundle."""
    # Name the file to edit: the env file, both launch files for an inline
    # token, or the mounted file itself.
    placeholders = placeholder_block(
        facts, o,
        {"auth_token": _docker_where(o["use_secret"]),
         **{m.option: m.file for m in docker_file_mounts(o)}})
    ignored_table = ignored_block(o)
    env_note = (f"""
- **Never rename `{DOCKER_ENV_FILE}` to `.env`.** Compose would use it for its
  own substitution and the token would not reach the agent.""") if o["use_secret"] else ""
    ca = ca_cfg(o)
    ca_block = ""
    if ca:
        ca_block = f"""
- **Trust.** `{DOCKER_CA_FILE}` is mounted at `{DOCKER_CA_PATH}` and replaces
  the container's CA store, so it must hold your CA *and* the public roots.
"""
    return f"""{bundle_table(facts, o)}{placeholders}{ca_slot_block(o)}
## Run it

Use one of these, not both -- they start the same container.

```
./{DOCKER_RUN_FILE}
```

```
docker compose up -d          # needs `docker compose version` 2 or newer
```

The agent should show online in BlazeMeter within a minute or two (longer on
the first run, which pulls crane). Watch it with
`docker logs -f {docker_container_name(o["ship_id"])}`.

## Worth knowing

{sizing_bullet(facts, o)}{location_bullet(facts, o)}
{_docker_socket_bullet(facts, o)}
- **One container per agent.** Both files name the container
  `{docker_container_name(o["ship_id"])}`, so a second start is refused. Two
  containers for one agent would produce duplicated results.{env_note}
{_docker_beside_bullet(facts, o)}{ca_block}{_docker_sv_block(facts, o)}{_docker_prepull_block(facts, o)}
- This is BlazeMeter's Docker Command for this agent with this bundle's
  settings added.
{ignored_table}"""


def render(facts, o):
    """The docker bundle as {filename: content}, profile.json aside."""
    out = {DOCKER_RUN_FILE: _docker_run_sh(facts, o),
           DOCKER_COMPOSE_FILE: _docker_compose_yaml(facts, o)}
    env_file = _docker_env_file(facts, o)
    if env_file:
        out[DOCKER_ENV_FILE] = env_file
    # A mount with no content (the file mode) is still wired; the script's
    # existence check refuses to start until the file is there.
    for m in docker_file_mounts(o):
        if m.content is not None:
            out[m.file] = m.content
    if o["private_registry"]:
        out[MIRROR_SCRIPT_FILE] = mirror_script(facts, o)
    out["README.md"] = _docker_readme(facts, o)
    return out
