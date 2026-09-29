"""Is a directory the bundle a live run was told to test, and can the rig deploy it?

Pure reads of files a `generate` wrote, made before a cluster is built or a
container started: `--manifests` defaults to out/, which holds whatever the last
`generate` left there, and deploying another agent's bundle fails only after a
12-20 minute run, as an agent that never comes online.

Fields are read with regexes rather than YAML: PyYAML would be a second runtime
dependency, to read a few fields out of files this generator wrote itself.
"""

import collections
import glob
import os
import re

from . import generate

CONFIGMAP_FILE = "bzm_configmap.yaml"

# HARBOR_ID / SHIP_ID as the ConfigMap template writes them. compose.yaml's
# environment block uses the same `NAME: "value"` shape, so this reads both.
_IDENTITY_RE = re.compile(r'^\s*(HARBOR_ID|SHIP_ID):\s*"?([^"\s]+)"?\s*$', re.M)
_COMPOSE_NAME_RE = re.compile(r'^\s*container_name:\s*"?([^"\s]+)"?\s*$', re.M)
# A value nobody supplied, as _compose_required writes it; captures the name.
_COMPOSE_UNSET_RE = re.compile(
    r'\$\{' + re.escape(generate.COMPOSE_UNSET_PREFIX) + r'([A-Za-z0-9_]+):\?')

PLATFORM_MANIFESTS = "manifests"
PLATFORM_COMPOSE = "compose"


class BundleCheck(collections.namedtuple("BundleCheck", "refusals notes")):
    """What a directory said about itself: what stops the run, and what could
    not be looked at."""

    def report(self):
        """Print the notes; return the refusals as one message, or None. What a
        refusal costs is the caller's: the CLI exits, livetest raises."""
        for note in self.notes:
            print("note: " + note)
        return "\n".join(self.refusals) or None


class BundleMismatch(RuntimeError):
    """The manifests on disk are not the ones this run was told to test."""


def _file_text(path):
    """The file's text, or None where it could not be read (absent, or bytes
    that are not text). Callers pass None on rather than folding it into ""."""
    try:
        with open(path) as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def manifest_identity(manifest_dir):
    """{"HARBOR_ID", "SHIP_ID"} as the ConfigMap names them.

    None: the file could not be read. {}: read, and names neither. A key is
    absent where the file does not carry it."""
    text = _file_text(os.path.join(manifest_dir, CONFIGMAP_FILE))
    if text is None:
        return None
    return {m.group(1): m.group(2) for m in _IDENTITY_RE.finditer(text)}


def emitted_yaml_files():
    """Every *.yaml a manifests bundle from this generator can hold, from the
    generator's own constants. The chart's values file is deliberately absent:
    this rig deploys manifests."""
    return frozenset(generate.APPLY_ORDER) | {generate.HOOK_FILE,
                                              generate.SV_EXPOSE_FILE}


def bundle_yaml(manifest_dir):
    """The files deploy() would apply, by basename -- the same glob, so nothing
    applied goes unjudged. glob skips dotfiles, so the rig's own
    .egress-policy.yaml is neither applied nor judged."""
    return sorted(os.path.basename(p) for p in
                  glob.glob(os.path.join(manifest_dir, "*.yaml")))


def compose_path(manifest_dir):
    return os.path.join(manifest_dir, generate.DOCKER_COMPOSE_FILE)


def bundle_platform(manifest_dir, profile=None):
    """PLATFORM_COMPOSE or PLATFORM_MANIFESTS: which rig this directory needs.

    Read off the bundle rather than a flag, because both wrong answers are the
    same silent run (nothing applied, timeout waited out). The profile decides
    where there is one; a stray compose file in a manifests bundle is left for
    the unknown-*.yaml refusal. Without a profile, a compose file means docker.
    """
    if (profile or {}).get("output_format") == "docker":
        return PLATFORM_COMPOSE
    if profile:
        return PLATFORM_MANIFESTS
    return (PLATFORM_COMPOSE if os.path.exists(compose_path(manifest_dir))
            else PLATFORM_MANIFESTS)


def compose_identity(manifest_dir):
    """{"HARBOR_ID", "SHIP_ID", "container_name"} as compose.yaml names them;
    None, {} and absent keys mean what they mean for manifest_identity."""
    text = _file_text(compose_path(manifest_dir))
    if text is None:
        return None
    found = {m.group(1): m.group(2) for m in _IDENTITY_RE.finditer(text)}
    name = _COMPOSE_NAME_RE.search(text)
    if name:
        found["container_name"] = name.group(1)
    return found


def compose_unset(manifest_dir):
    """Variables still under compose's required-variable guard, sorted.

    Read off both files (use_secret decides which holds the credential), not
    off profile.json, which never carries auth_token."""
    names = set()
    for name in (generate.DOCKER_COMPOSE_FILE, generate.DOCKER_ENV_FILE):
        text = _file_text(os.path.join(manifest_dir, name)) or ""
        names.update(m.group(1) for m in _COMPOSE_UNSET_RE.finditer(text))
    return sorted(names)


BlankMounts = collections.namedtuple("BlankMounts", "blank unread")
# A mounted file left blank: the generator's mount, the path actually read,
# and the marker found in it (read off the file, which may be an override or an
# older version's output).
BlankMount = collections.namedtuple("BlankMount", "mount path marker")


def compose_blank_mounts(manifest_dir):
    """Mounted files that still carry a marker, and those that could not be read.

    A blank *file* option (sv_tls_key, sv_tls_cert, ca_bundle) puts the marker
    in the file's bytes, where neither compose_unset nor profile.json can see
    it. Each path resolves through its override variable exactly as the script
    does, since `compose up` inherits this environment and would mount the
    override. A file absent with no override is not a mount this bundle has.
    """
    blank, unread = [], []
    for m in generate.DOCKER_FILE_MOUNTS:
        override = os.environ.get(m.var)
        path = override or os.path.join(manifest_dir, m.file)
        if not override and not os.path.exists(path):
            continue
        text = _file_text(path)
        if text is None:
            unread.append((m, path))
            continue
        mark = generate.marker_in(text)
        if mark:
            blank.append(BlankMount(m, path, mark))
    return BlankMounts(blank, unread)


def bundle_check(manifest_dir, harbor_id, ship_id, profile=None):
    """Is this directory the bundle this run was told to test?

    Returns refusals (deploy nothing) and notes (what could not be checked).
    A manifests bundle records its identity in its ConfigMap; a compose bundle
    in compose.yaml's environment and container name.
    """
    if bundle_platform(manifest_dir, profile) == PLATFORM_COMPOSE:
        return _compose_bundle_check(manifest_dir, harbor_id, ship_id, profile)
    refusals, notes = [], []
    path = os.path.join(manifest_dir, CONFIGMAP_FILE)
    claimed = manifest_identity(manifest_dir)
    if claimed is None:
        notes.append(
            f"{path} could not be read, so this bundle's identity was not "
            f"checked against harbor {harbor_id} / ship {ship_id}")
        claimed = {}
    elif not claimed:
        notes.append(
            f"{path} carries no HARBOR_ID/SHIP_ID, so this bundle's identity "
            f"was not checked against harbor {harbor_id} / ship {ship_id}")
    for field, want in (("HARBOR_ID", harbor_id), ("SHIP_ID", ship_id)):
        got = claimed.get(field)
        if claimed and got is None:
            notes.append(f"{path} names no {field}, so it was not checked "
                         f"against {want}")
        elif got and want and got != want:
            refusals.append(
                f"{path} names {field} {got}, but this run was told to test "
                f"{want}. The directory holds the bundle for a different agent: "
                f"crane would come up with an identity BlazeMeter will not "
                f"register, the rollout would time out saying only that, and "
                f"the cluster would be deleted with nothing left to read. "
                f"Re-generate into {manifest_dir}/, or point --manifests at the "
                f"bundle built for {want}")
    refusals += _profile_refusals(manifest_dir, ship_id, profile)
    unknown = [n for n in bundle_yaml(manifest_dir)
               if n not in emitted_yaml_files()]
    if unknown:
        # Refused, not warned: the file is applied and changes the run (a stale
        # bzm_limitrange.yaml capped crane's job pods), and a warning scrolls
        # past in the first seconds of a long run.
        refusals.append(
            f"{manifest_dir}/ holds {', '.join(unknown)}, which this generator "
            f"does not emit -- and livetest applies every *.yaml in the "
            f"directory, so an older version's leftovers are deployed as part "
            f"of the run and the run stops being a test of generator output. "
            f"Delete them, or generate into an empty directory")
    return BundleCheck(refusals, notes)


def _plural(n, one, many):
    return many if n > 1 else one


def _profile_refusals(manifest_dir, ship_id, profile):
    """What profile.json alone says is wrong, on either platform."""
    refusals = []
    path = os.path.join(manifest_dir, generate.PROFILE_FILE)
    # A re-render merges onto the profile and prefers its ship_id, so a stale
    # one deploys the wrong agent even on a path that re-renders.
    prof_ship = (profile or {}).get("ship_id")
    if prof_ship and ship_id and prof_ship != ship_id:
        refusals.append(
            f"{path} was generated for ship {prof_ship}, not the {ship_id} "
            f"this run was told to test -- the directory is another agent's "
            f"bundle, and a re-render would merge onto it rather than "
            f"correct it")
    # The API server refuses a marker too, but only after the cluster is built.
    blank = generate.placeholder_options(profile or {})
    if blank:
        n = len(blank)
        named = ", ".join(f"{k} ({generate.marker(k)})" for k in blank)
        refusals.append(
            f"{path} was generated with {named} left blank, so the bundle "
            f"carries {_plural(n, 'that marker', 'those markers')} "
            f"instead of {_plural(n, 'that value', 'those values')}. "
            f"Nothing here can guess {_plural(n, 'it', 'them')}: "
            f"re-generate the bundle with {_plural(n, 'it', 'them')} set")
    return refusals


def _compose_bundle_check(manifest_dir, harbor_id, ship_id, profile):
    """bundle_check for a docker bundle, over the files it actually has."""
    refusals, notes = [], []
    path = compose_path(manifest_dir)
    if not os.path.exists(path):
        # profile.json says docker and the compose file is gone: an older
        # bundle, or a tidied directory. There is nothing to start.
        return BundleCheck([
            f"{manifest_dir}/ is a docker bundle with no "
            f"{generate.DOCKER_COMPOSE_FILE} in it, and this run starts a "
            f"docker bundle with `docker compose up`. Re-generate it: "
            f"{generate.DOCKER_RUN_FILE} on its own is the other route, and "
            f"the two are either/or rather than interchangeable here"], [])
    claimed = compose_identity(manifest_dir)
    want_name = generate.docker_container_name(ship_id) if ship_id else None
    # The file exists, so None is a file nothing could decode: one note, and no
    # per-field notes, which would be claims about a file somebody read.
    read = claimed is not None
    if not read:
        notes.append(
            f"{path} could not be read, so nothing in it was checked -- not the "
            f"container name against {want_name}, and not the identity against "
            f"harbor {harbor_id} / ship {ship_id}")
        claimed = {}
    got_name = claimed.get("container_name")
    if got_name is None and read:
        notes.append(f"{path} names no container_name, so it was not checked "
                     f"against {want_name}")
    elif want_name and got_name and got_name != want_name:
        refusals.append(
            f"{path} starts a container called {got_name}, but this run was "
            f"told to test ship {ship_id}, whose container is {want_name}. The "
            f"directory holds the bundle for a different agent: crane would "
            f"come up with an identity BlazeMeter will not register, and this "
            f"run would wait out its whole timeout reporting only that the "
            f"agent never came online. Re-generate into {manifest_dir}/, or "
            f"point --manifests at the bundle built for {ship_id}")
    for field, want in (("HARBOR_ID", harbor_id), ("SHIP_ID", ship_id)):
        got = claimed.get(field)
        if got is None:
            if read:
                notes.append(f"{path} names no {field}, so it was not checked "
                             f"against {want}")
        elif got and want and got != want:
            refusals.append(
                f"{path} names {field} {got}, but this run was told to test "
                f"{want}. Re-generate into {manifest_dir}/, or point "
                f"--manifests at the bundle built for {want}")
    # `compose up` refuses these too, but mid-run, after a container has been
    # created against a real account.
    unset = compose_unset(manifest_dir)
    if unset:
        it = _plural(len(unset), "it", "them")
        refusals.append(
            f"{manifest_dir}/ still carries compose's required-variable guard "
            f"for {', '.join(unset)}, which is what this generator writes where "
            f"a required value was left blank. `docker compose up` refuses it "
            f"and so does this: fill {it} in, or re-generate the bundle with "
            f"{it} set")
    mounts = compose_blank_mounts(manifest_dir)
    for m, at in mounts.unread:
        # No marker named: nothing read the file, so nothing knows its content.
        notes.append(f"{at} could not be read, so it was not checked for a "
                     f"marker -- the {m.what} this bundle mounts is whatever "
                     f"that file holds")
    if mounts.blank:
        files = ", ".join(f"{b.path} ({b.marker})" for b in mounts.blank)
        opts = ", ".join(b.mount.option for b in mounts.blank)
        names = ", ".join(b.mount.var for b in mounts.blank)
        many = len(mounts.blank) > 1
        refusals.append(
            f"{files} carr{'y' if many else 'ies'} a marker, "
            f"which is what this generator writes into a mounted file whose "
            f"option was left blank ({opts}). The container would come up and "
            f"fail on it later -- a rejected handshake rather than an agent "
            f"that never appears -- so the run would report the wrong thing "
            f"about the wrong bundle. Set "
            f"{names} to {'files' if many else 'a file'} this host already has, "
            f"or re-generate the bundle with {opts} filled in")
    refusals += _profile_refusals(manifest_dir, ship_id, profile)
    return BundleCheck(refusals, notes)


# -- CA trust modes the rig can deploy ----------------------------------------

# The rig's own trust-bundle ConfigMap for `--ca-mode existing`. The key is not
# `ca-bundle.crt`, _ca_cfg's fallback, or a run would pass whether or not the
# configured key reached anything.
CA_RIG_CONFIGMAP = "bzm-opl-livetest-trust"
CA_RIG_KEY = "corp-root.pem"

# generate's CA modes minus `inject`, which the OpenShift network operator
# performs and nothing here can. Also `--ca-mode`'s choices.
RIG_CA_MODES = ("inline", "existing", "file")


def rig_ca_mode(profile):
    """The `--ca-mode` a bundle is already generated for, or None where it
    carries no mode the rig can build (no CA trust, or OpenShift injection).

    Callers run ca_configmap_refusal first, so generate.CA_UNRESOLVED never
    reaches here."""
    mode = generate.ca_mode(profile)
    return mode if mode in RIG_CA_MODES else None


def resolved_ca_mode(profile, asked=None):
    """The CA mode a run deploys: what was asked, else the bundle's own, else
    inline. Shared by the CLI (before the credential mint) and run()."""
    return asked or rig_ca_mode(profile) or "inline"


def ca_mode_notice(profile, chosen):
    """What a run says about the CA mode it is about to deploy. Never a refusal:
    `--ca-mode` may deliberately test another mode, but not silently."""
    carried = rig_ca_mode(profile)
    if carried == chosen:
        # `existing` deploys under the rig's own ConfigMap, not the customer's.
        under = (f" -- under {CA_RIG_CONFIGMAP}, this rig's own ConfigMap "
                 f"rather than the one the bundle names"
                 if chosen == "existing" else "")
        return (f"CA mode under test: {chosen}, which is what this bundle was "
                f"generated for{under}")
    if carried:
        return (f"note: this bundle is generated for the {carried} CA mode, and "
                f"--ca-mode {chosen} replaces it -- the run tests {chosen}, not "
                f"what is on disk")
    mode = generate.ca_mode(profile)
    why = ("is generated for OpenShift trust injection, which this rig cannot "
           "deploy -- nothing here injects a trust bundle"
           if mode == "inject" else
           "configures no CA trust, and a run under the proxy needs some -- "
           "the CA under test is the proxy's own")
    return f"note: this bundle {why}, so the run deploys the {chosen} mode instead"


def ca_configmap_refusal(profile, local_proxy):
    """Why this run cannot deploy this bundle's CA trust, or None.

    `file` and `existing` name a ConfigMap the bundle does not create, and the
    rig's namespace will not have it: the crane pod would sit at
    ContainerCreating for the whole timeout. `--local-proxy` builds one itself,
    so it answers None there. `inject` is not refused: its ConfigMap is emitted
    (empty), so the pod starts and the failure is a readable TLS error.
    """
    ca = generate.resolved_ca(profile)
    if ca is generate.CA_UNRESOLVED:
        return (f"{generate.PROFILE_FILE} sets more than one CA mode, and the "
                f"generator takes one: a run that re-renders would raise out of "
                f"generate() with the cluster already built, and one that does "
                f"not would deploy manifests whose own profile disagrees with "
                f"them. Re-generate the bundle with a single CA mode set "
                f"({', '.join(generate.CA_MODES)})")
    if local_proxy or not ca or ca["mode"] not in ("file", "existing"):
        return None
    if ca["mode"] == "file":
        carries = (f"names the certificate file {ca['key']} and creates no "
                   f"ConfigMap, so the crane pod mounts {ca['cm']}, which a "
                   f"customer's pipeline builds from that certificate")
        instead = ("builds that ConfigMap from the proxy's own CA the way that "
                   "pipeline does")
    else:
        carries = (f"references the existing ConfigMap {ca['cm']}, which a "
                   f"platform team owns and this bundle does not create")
        instead = ("creates a trust ConfigMap of the rig's own and re-renders "
                   "the bundle to reference it")
    return (
        f"{generate.PROFILE_FILE} says this bundle {carries}. livetest creates "
        f"no such ConfigMap, and deploys into a namespace it has usually just "
        f"made itself: the crane pod would sit at ContainerCreating naming it, "
        f"no heartbeat could arrive, and the run would spend its whole timeout "
        f"reporting only that the agent never came online. Add --local-proxy "
        f"--ca-mode {ca['mode']}: it {instead}, and it is the only run that "
        f"tests this mode at all. Or re-generate the bundle with the PEM inline "
        f"(--ca-bundle), which carries its own ConfigMap")
