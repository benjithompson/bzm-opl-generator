"""The public entry point: render a bundle from facts and options, and read one
back.

generate() resolves the options and hands off to render_manifests, render_helm
or render_docker.
"""

import json
import os
import re

from . import (image_catalog, render_docker, render_helm, render_manifests,
               security_review)
from .bundle_env import extra_env
from .bundle_names import (APPLY_ORDER, CHART_DIR, CONFIGMAP_FILE,
                           DOCKER_CA_FILE, DOCKER_COMPOSE_FILE,
                           DOCKER_ENV_FILE, DOCKER_RUN_FILE,
                           DOCKER_SV_CERT_FILE, DOCKER_SV_KEY_FILE,
                           HELM_CHART_FILE, HELM_VALUES_FILE, IMAGES_FILE,
                           PREVIEW_TAIL, PROFILE_FILE, REVIEW_FILE,
                           SECRET_FILE)
from .bundle_options import (DEFAULT_OPTIONS, OUTPUT_FORMATS,
                             auto_update, engine_size,
                             resolve_engine_limits, service_account)
from .ca_trust import ca_cfg
from .markers import is_placeholder, or_marker
from .required_fields import fill_placeholders
from .service_virt import sv_cfg


def generate(facts, options):
    """Return {filename: content}; `options` override DEFAULT_OPTIONS.

    Names may contain `/` (the helm format emits a chart directory); write()
    creates the parent directories.
    """
    o = {**DEFAULT_OPTIONS, **options}
    # Resolved into `o`, so the manifests, the READMEs and profile.json all
    # carry the same value.
    o.update(resolve_engine_limits(facts, o))
    # Before the validators, so a blank required field renders as its marker
    # instead of being refused.
    fill_placeholders(o)
    if not str(o.get("ship_id") or "").strip():
        ships = facts.get("ships") or []
        if len(ships) == 1:
            o["ship_id"] = ships[0]["id"]
        else:
            # Several agents is a question, not a blank: guessing could bind
            # the bundle to an identity somebody is already running.
            raise ValueError(
                f"ship_id required: location has {len(ships)} ships "
                f"({[s['id'] for s in ships]})"
            )
    # The identity is blank when the bundle is wanted before the location
    # exists. `facts` is copied, not written through.
    o["ship_id"] = or_marker(o["ship_id"], "ship_id")
    facts = {**facts, "harbor_id": or_marker(facts.get("harbor_id"), "harbor_id")}

    if o["output_format"] not in OUTPUT_FORMATS:
        raise ValueError(f"output_format must be one of {OUTPUT_FORMATS}, "
                         f"got {o['output_format']!r}")

    # Refuse bad values now rather than at apply time. Each validator asks
    # ignored_options() first, so none refuses a field its format lacks.
    engine_size(o)
    sa = service_account(o)
    auto_update(o)
    extra_env(o)
    ca = ca_cfg(o)
    sv = sv_cfg(facts, o)

    if o["output_format"] == "docker":
        out = render_docker.render(facts, o)
        objects = None
    elif o["output_format"] == "helm":
        out = render_helm.render(facts, o, ca)
        # The chart renders the same objects as the manifests (held equal by
        # tests/helm_parity.py), so the review reads those.
        objects = render_manifests.render(facts, o, ca, sv, sa)
    else:
        out = render_manifests.render(facts, o, ca, sv, sa)
        objects = out
    out[IMAGES_FILE] = image_catalog.images_md(facts, o)
    out[REVIEW_FILE] = security_review.review_md(facts, o, objects)
    out[PROFILE_FILE] = profile_json(o)
    return out


def preview_order(files):
    """The order to list generated files in for a human: the manifests in apply
    order, the chart's values overlay first, the docker command first.
    """
    if HELM_CHART_FILE in files:
        lead = [HELM_VALUES_FILE, "README.md", f"{CHART_DIR}/README.md",
                HELM_CHART_FILE, f"{CHART_DIR}/values.yaml"]
    elif DOCKER_RUN_FILE in files:
        # BlazeMeter's own shape (the script) leads the compose file.
        lead = ([DOCKER_RUN_FILE, DOCKER_COMPOSE_FILE, DOCKER_ENV_FILE,
                 DOCKER_CA_FILE, DOCKER_SV_CERT_FILE, DOCKER_SV_KEY_FILE]
                + PREVIEW_TAIL)
    else:
        lead = APPLY_ORDER + PREVIEW_TAIL
    return [n for n in lead if n in files] + sorted(set(files) - set(lead))


def write(files, outdir):
    """Write `files` under `outdir`, creating parent directories, and make
    every .sh executable. Returns the sorted names.
    """
    os.makedirs(outdir, exist_ok=True)
    for name, content in files.items():
        path = os.path.join(outdir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        if name.endswith(".sh"):
            os.chmod(path, os.stat(path).st_mode | 0o111)
    return sorted(files)


# Options whose value is a credential: profile.json omits them and the MCP
# server never echoes them. sv_tls_cert is public and stays, so a replayed
# docker-SV profile needs only --auth-token and --sv-tls-key.
SECRET_OPTIONS = frozenset({"auth_token", "sv_tls_key"})


def profile_json(o):
    """profile.json: the resolved options minus credentials, for replay with
    --profile. A regenerate reads the token back from the bundle instead
    (core.resolve_auth_token).
    """
    return json.dumps(
        {k: v for k, v in sorted(o.items()) if k not in SECRET_OPTIONS},
        indent=2) + "\n"


def load_profile(outdir):
    """Read back the profile.json generate() wrote next to the manifests."""
    path = os.path.join(outdir, PROFILE_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found -- regenerate the manifests with a current "
            f"bzm-opl-gen so the options can be replayed")
    with open(path) as f:
        return json.load(f)


# Every field a bundle carries the AUTH_TOKEN under: the variable's own name
# and the chart value's. existing_auth_token and core.redact_tokens both read
# it.
TOKEN_FIELDS = ("AUTH_TOKEN", "authToken")

# The files those fields are written into.
TOKEN_FILES = (SECRET_FILE, CONFIGMAP_FILE, HELM_VALUES_FILE)

AUTH_TOKEN_RE = re.compile(
    r'^\s*(?:' + "|".join(TOKEN_FIELDS) + r'):\s*"?([^"\s]+)"?\s*$', re.M)


def existing_auth_token(output_dir):
    """The AUTH_TOKEN already in the bundle at `output_dir`, or None.

    Read back because fetching one mints a new token and revokes the running
    agent's, which then answers 404, logs `Sleeping for 300` and looks like a
    slow boot. A marker, from any version, is not a token.
    """
    for name in TOKEN_FILES:
        try:
            with open(os.path.join(output_dir, name)) as fh:
                m = AUTH_TOKEN_RE.search(fh.read())
        except OSError:
            continue
        if m and not is_placeholder(m.group(1)):
            return m.group(1)
    return None
