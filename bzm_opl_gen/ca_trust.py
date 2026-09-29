"""CA trust: which mode a bundle uses, and the ConfigMap and file it names.

existing: a ConfigMap the platform team owns. inline: the bundle writes the
ConfigMap from a PEM. file: the bundle names a certificate file and the
ConfigMap built from it, and carries neither. inject: OpenShift fills a labeled
ConfigMap with the cluster trust bundle.
"""

from .bundle_names import CA_CONFIGMAP, CHART_DIR, DOCKER_RUN_FILE
from .bundle_options import DEFAULT_OPTIONS, ignored_options
from .markers import is_placeholder, marker


# Crane mounts the CA ConfigMap as a directory here; engines get the key as a
# file via KUBERNETES_CA_BUNDLE_MOUNT.
CA_MOUNT_PATH = "/var/cm"

# The key OpenShift's injection writes, and the default key elsewhere.
CA_FILENAME = "ca-bundle.crt"

# The options that pick a CA mode, and each mode in words. At most one may be
# set.
CA_MODES = {
    "ca_existing_configmap": "an existing ConfigMap",
    "ca_bundle": "an inline PEM",
    "ca_bundle_slot": "a certificate file the ConfigMap is built from",
    "ca_openshift_inject": "OpenShift injection",
}

# Every CA option, for a caller that removes CA trust rather than picking a
# mode.
CA_OPTIONS = tuple(CA_MODES) + ("ca_configmap_key", "ca_cert_file")


def no_ca():
    """Every CA option at its own default: an overlay that removes CA trust.

    The defaults differ (False and None), so they come from DEFAULT_OPTIONS and
    profile.json only ever holds values a form could produce.
    """
    return {k: DEFAULT_OPTIONS[k] for k in CA_OPTIONS}


def ca_cfg(o):
    """Resolve CA trust to {cm, key, mode}, or None.

    Modes the format ignores are not counted, so a Kubernetes bundle switched
    to docker is not refused over a ConfigMap name docker's page does not show.
    """
    ignored = ignored_options(o)
    active = [k for k in CA_MODES if o[k] and k not in ignored]
    # Named as a pair: the generic message would send somebody to the wrong
    # control.
    if "ca_bundle_slot" in active and "ca_bundle" in active:
        raise ValueError(
            "ca_bundle_slot leaves the PEM to be filled in later, and ca_bundle "
            "supplies it now -- set one. Drop ca_bundle_slot to use the "
            "certificate you have, or drop ca_bundle to hand over a bundle "
            "waiting for one.")
    if len(active) > 1:
        raise ValueError("choose one CA mode: " + " | ".join(
            f"{k} ({what})" for k, what in CA_MODES.items()))
    if not active:
        return None
    if active[0] == "ca_existing_configmap":
        return {"cm": o["ca_existing_configmap"],
                "key": o["ca_configmap_key"] or CA_FILENAME, "mode": "existing"}
    # Inject's key is what OpenShift's operator writes. Inline writes the file
    # itself, so a blank name defaults; the file mode names a file somebody
    # else supplies, so a blank name is the marker.
    mode = {"ca_bundle": "inline", "ca_bundle_slot": "file"}.get(
        active[0], "inject")
    key = {
        "inject": CA_FILENAME,
        "inline": o["ca_cert_file"] or CA_FILENAME,
        "file": o["ca_cert_file"] or marker("ca_cert_file"),
    }[mode]
    return {"cm": CA_CONFIGMAP, "key": key, "mode": mode}


# resolved_ca's answer for options ca_cfg refuses (two modes). A string, never
# None: None means no CA trust, the opposite fact.
CA_UNRESOLVED = "unresolved"


def resolved_ca(options):
    """The CA trust raw options resolve to: {cm, key, mode}, None, or
    CA_UNRESOLVED.

    Merges DEFAULT_OPTIONS itself and never raises: its callers describe a
    bundle (the MCP warnings, livetest's guard), and generate() makes the
    refusal.
    """
    try:
        return ca_cfg({**DEFAULT_OPTIONS, **(options or {})})
    except ValueError:
        return CA_UNRESOLVED


def ca_mode(options):
    """resolved_ca's mode alone: existing, inline, file, inject, None or
    CA_UNRESOLVED.
    """
    ca = resolved_ca(options)
    if ca is CA_UNRESOLVED:
        return ca
    return ca["mode"] if ca else None


def ca_slot_notice(options):
    """The generate-time line about a certificate the bundle names but does not
    carry, or None.

    The README says it again for whoever applies the bundle. None for anything
    generate() will refuse: a notice must not raise.
    """
    o = {**DEFAULT_OPTIONS, **options}
    ca = resolved_ca(options)
    if ca is CA_UNRESOLVED or not ca or ca["mode"] != "file":
        return None
    # A named file is a finished bundle waiting on a planned step; an unnamed
    # one is unfinished.
    named = (f"names {ca['key']}" if not is_placeholder(ca["key"]) else
             f"carries {marker('ca_cert_file')} -- nobody has said what the "
             f"certificate is called")
    builds = {
        "helm": (f"Put the PEM in {CHART_DIR}/ under that name; helm install "
                 f"builds the {CA_CONFIGMAP} ConfigMap from it, and refuses "
                 f"while it is missing."),
        "docker": (f"Put the PEM beside ./{DOCKER_RUN_FILE}, which refuses to "
                   f"start without it."),
        "manifests": (f"Create the {CA_CONFIGMAP} ConfigMap from it before "
                      f"kubectl apply -- the README prints the command. Until "
                      f"then the crane pod sits at ContainerCreating and names "
                      f"the ConfigMap it cannot mount."),
    }.get(o["output_format"])
    if not builds:
        return None
    return (f"CA certificate: this bundle {named}. {builds} Before deploying, "
            f"bzm-opl-gen ca-check tests that file against the chain the "
            f"agent's network presents.")
