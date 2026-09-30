"""Reads from a Docker Registry v2: a tag's digest, its compressed size and
the repository's tags. Used against BlazeMeter's public registry (anonymous)
and against a customer's mirror (with their credentials).

Every read answers with a state, never an exception: `read` (the registry
answered), or `unread` with a detail (refused, timed out, unreachable). A 404
is an answer -- the tag is not there -- and never shares a representation with
a read that failed.
"""

import base64
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

READ = "read"
UNREAD = "unread"
NOT_ASKED = "not-asked"

# What a mirror check finds per image.
PRESENT = "present"
MISSING = "missing"

# Short: a lookup is a courtesy beside the catalogue, not something to wait on.
TIMEOUT_S = 6

# Docker's own index host, for a reference with no registry host in it.
DOCKER_HUB = "registry-1.docker.io"

MANIFEST_ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")

# BlazeMeter's images are amd64-only; a multi-arch index is sized for this.
SIZE_PLATFORM = ("linux", "amd64")

# A tag list is paged by `Link`; more pages than this is not a real repository.
MAX_TAG_PAGES = 20

# A floating tag is named by comparing its digest with at most this many of
# the newest versioned tags, within this many seconds.
RESOLVE_CANDIDATES = 10
RESOLVE_BUDGET_S = 15

USER_ENV = "BZM_REGISTRY_USER"
PASSWORD_ENV = "BZM_REGISTRY_PASSWORD"
DOCKER_CONFIG = os.path.join("~", ".docker", "config.json")


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        # Header names compared lower-case.
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body or b""


class Unreachable(Exception):
    """No HTTP answer at all: DNS, TLS, refused connection, timeout."""


def http_request(method, url, headers, timeout, context):
    """One HTTP exchange. An HTTP error status is a Response; only a failure
    to get an answer raises Unreachable. Tests replace this function."""
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
            return Response(r.status, dict(r.headers), r.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, dict(e.headers or {}), e.read() or b"")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Unreachable(str(getattr(e, "reason", e)))


# -- references ---------------------------------------------------------------

def split_ref(ref):
    """(scheme, host, repository path, tag) for an image reference. A leading
    `http://` marks a registry that serves plain HTTP; the default is https."""
    scheme = "https"
    for prefix in ("http://", "https://"):
        if ref.startswith(prefix):
            scheme, ref = prefix[:-3], ref[len(prefix):]
    name, sep, tag = ref.rpartition(":")
    if not sep or "/" in tag:
        name, tag = ref, "latest"
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        host, path = first, rest
    else:
        host, path = DOCKER_HUB, name if "/" in name else f"library/{name}"
    return scheme, host, path, tag


def is_loopback(host):
    """localhost or 127.0.0.0/8, with or without a port: the registries
    docker pushes to without TLS unless told otherwise."""
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if name == "localhost":
        return True
    parts = name.split(".")
    return (len(parts) == 4 and parts[0] == "127"
            and all(p.isdigit() and int(p) < 256 for p in parts))


def registry_scheme(registry):
    """The scheme to speak to a registry prefix: its own `http://` or
    `https://`, else docker's rule -- plain HTTP for localhost and
    127.0.0.0/8, HTTPS for every other host."""
    for prefix in ("http://", "https://"):
        if registry.startswith(prefix):
            return prefix[:-3]
    return "http" if is_loopback(strip_scheme(registry).split("/")[0]) \
        else "https"


def strip_scheme(registry):
    """A registry prefix as an image reference writes it: no scheme, no
    trailing slash."""
    for prefix in ("http://", "https://"):
        if registry.startswith(prefix):
            registry = registry[len(prefix):]
    return registry.rstrip("/")


# -- credentials ---------------------------------------------------------------

def credentials_for(host, environ=None, config_path=None):
    """(user, password, where) for `host`: the environment first, then an
    inline `auth` in the docker config. (None, None, why) when neither has one.
    Credential helpers are not called."""
    env = os.environ if environ is None else environ
    user, password = env.get(USER_ENV), env.get(PASSWORD_ENV)
    if user and password:
        return user, password, f"{USER_ENV}/{PASSWORD_ENV}"
    path = os.path.expanduser(config_path or DOCKER_CONFIG)
    try:
        with open(path) as f:
            config = json.load(f)
    except (OSError, ValueError):
        return None, None, "no credentials in the environment or the docker config"
    auths = config.get("auths") or {}
    for key in (host, f"https://{host}", f"http://{host}", f"https://{host}/v1/"):
        entry = auths.get(key) or {}
        if entry.get("auth"):
            try:
                user, _, password = base64.b64decode(entry["auth"]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                continue
            return user, password, path
    if config.get("credsStore") or config.get("credHelpers"):
        return None, None, (f"the docker config keeps credentials in a helper, "
                            f"which is not called; set {USER_ENV} and "
                            f"{PASSWORD_ENV}")
    return None, None, f"no credentials for {host} in the environment or {path}"


# -- the client ------------------------------------------------------------------

class Registry:
    """One registry host. Bearer tokens are fetched per repository scope and
    kept for the life of the object."""

    def __init__(self, host, scheme="https", credentials=None, ca_file=None,
                 timeout=TIMEOUT_S):
        self.host, self.scheme, self.timeout = host, scheme, timeout
        self.credentials = credentials
        self.context = None
        if scheme == "https":
            self.context = ssl.create_default_context(cafile=ca_file)
        self._tokens = {}

    def _basic(self):
        user, password = self.credentials
        raw = base64.b64encode(f"{user}:{password}".encode()).decode()
        return f"Basic {raw}"

    def _call(self, method, url, headers):
        return http_request(method, url, headers, self.timeout, self.context)

    def _token(self, challenge, path):
        """A bearer token for `path` from the challenge's realm, or (None,
        detail)."""
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.get("realm")
        if not realm:
            return None, f"the registry's challenge names no realm: {challenge}"
        query = {"scope": f"repository:{path}:pull"}
        if params.get("service"):
            query["service"] = params["service"]
        headers = {"Authorization": self._basic()} if self.credentials else {}
        try:
            r = self._call("GET", f"{realm}?{urllib.parse.urlencode(query)}",
                           headers)
        except Unreachable as e:
            return None, f"the token service at {realm} did not answer: {e}"
        if r.status != 200:
            return None, f"the token service refused ({r.status})"
        try:
            body = json.loads(r.body)
        except ValueError:
            return None, "the token service answered with something not JSON"
        token = body.get("token") or body.get("access_token")
        return (token, None) if token else (None, "the token service sent no token")

    def get(self, method, path, suffix, accept=None):
        """(Response, None) or (None, detail). Answers a 401 once, with a
        bearer token or basic credentials, as the challenge asks."""
        url = f"{self.scheme}://{self.host}/v2/{path}/{suffix}"
        headers = {"Accept": accept} if accept else {}
        if path in self._tokens:
            headers["Authorization"] = self._tokens[path]
        try:
            r = self._call(method, url, headers)
            if r.status == 401 and "authorization" not in {k.lower() for k in headers}:
                challenge = r.headers.get("www-authenticate", "")
                if challenge.lower().startswith("bearer"):
                    token, detail = self._token(challenge, path)
                    if token is None:
                        return None, detail
                    self._tokens[path] = f"Bearer {token}"
                elif challenge.lower().startswith("basic") and self.credentials:
                    self._tokens[path] = self._basic()
                else:
                    return None, _denied(r.status, self.credentials)
                r = self._call(method, url, {**headers,
                                             "Authorization": self._tokens[path]})
        except Unreachable as e:
            return None, f"{self.host} did not answer: {e}"
        return r, None

    # -- reads -----------------------------------------------------------------

    def check(self, path, tag):
        """{"state": present|missing|unread, "detail", "digest"} for one tag,
        by HEAD. 401 and 403 are unread: a registry that refuses does not
        say whether the tag is there."""
        r, detail = self.get("HEAD", path, f"manifests/{tag}", MANIFEST_ACCEPT)
        if r is None:
            return {"state": UNREAD, "detail": detail, "digest": None}
        if r.status == 200:
            return {"state": PRESENT, "detail": None,
                    "digest": r.headers.get("docker-content-digest")}
        if r.status == 404:
            return {"state": MISSING, "detail": "the registry has no such tag",
                    "digest": None}
        if r.status in (401, 403):
            return {"state": UNREAD, "detail": _denied(r.status, self.credentials),
                    "digest": None}
        return {"state": UNREAD, "detail": f"the registry answered {r.status}",
                "digest": None}

    def manifest(self, path, tag):
        """{"state", "detail", "found", "digest", "size_mb"}. `found` False
        (state read) is a 404; size is the compressed layers of the linux/amd64
        image, None when the manifest does not list them."""
        out = {"state": UNREAD, "detail": None, "found": None, "digest": None,
               "size_mb": None}
        r, detail = self.get("GET", path, f"manifests/{tag}", MANIFEST_ACCEPT)
        if r is None:
            return {**out, "detail": detail}
        if r.status == 404:
            return {**out, "state": READ, "found": False,
                    "detail": "the registry has no such tag"}
        if r.status != 200:
            return {**out, "detail": _denied(r.status, self.credentials)
                    if r.status in (401, 403) else f"the registry answered {r.status}"}
        try:
            body = json.loads(r.body)
        except ValueError:
            return {**out, "detail": "the manifest is not JSON"}
        out.update(state=READ, found=True,
                   digest=r.headers.get("docker-content-digest"))
        media = body.get("mediaType") or r.headers.get("content-type", "")
        if media in INDEX_TYPES or "manifests" in body:
            child = next((m for m in body.get("manifests") or []
                          if (m.get("platform") or {}).get("os") == SIZE_PLATFORM[0]
                          and (m.get("platform") or {}).get("architecture")
                          == SIZE_PLATFORM[1]), None)
            if child is None:
                out["detail"] = "the image has no linux/amd64 variant"
                return out
            cr, detail = self.get("GET", path, f"manifests/{child['digest']}",
                                  MANIFEST_ACCEPT)
            if cr is None or cr.status != 200:
                out["detail"] = detail or f"the amd64 manifest answered {cr.status}"
                return out
            try:
                body = json.loads(cr.body)
            except ValueError:
                out["detail"] = "the amd64 manifest is not JSON"
                return out
        layers = body.get("layers")
        if isinstance(layers, list) and layers:
            out["size_mb"] = round(sum(int(l.get("size") or 0) for l in layers) / 1e6)
        return out

    def tags(self, path):
        """{"state", "detail", "tags"}; `tags` is None unless read."""
        found, suffix = [], "tags/list"
        for _ in range(MAX_TAG_PAGES):
            r, detail = self.get("GET", path, suffix)
            if r is None:
                return {"state": UNREAD, "detail": detail, "tags": None}
            if r.status != 200:
                return {"state": UNREAD, "tags": None,
                        "detail": f"the tag list answered {r.status}"}
            try:
                found += json.loads(r.body).get("tags") or []
            except ValueError:
                return {"state": UNREAD, "tags": None,
                        "detail": "the tag list is not JSON"}
            m = re.search(r"<[^>]*/v2/[^>]*?/(tags/list\?[^>]*)>",
                          r.headers.get("link", ""))
            if not m:
                return {"state": READ, "detail": None, "tags": found}
            suffix = m.group(1)
        return {"state": UNREAD, "tags": None,
                "detail": f"the tag list ran past {MAX_TAG_PAGES} pages"}


def _denied(status, credentials):
    who = "these credentials" if credentials else "an anonymous read"
    return f"the registry refused {who} ({status})"


# -- tag series ------------------------------------------------------------------

_VERSION = re.compile(r"^v?(\d+(?:\.\d+)+)(.*)$")


def series(tag):
    """(version tuple, suffix) for a versioned tag such as `2.4.533-reduced`;
    None for a tag with no version (`latest`)."""
    m = _VERSION.match(tag or "")
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split(".")), m.group(2)


def is_release(tag, rule):
    """Does `tag` have the shape `rule` (image_catalog.RELEASE_SERIES) calls a
    release: its version parts, its exact suffix, a last part below a bound?"""
    s = series(tag)
    if not s or s[1] != rule["suffix"] or len(s[0]) != rule["parts"]:
        return False
    return "last_below" not in rule or s[0][-1] < rule["last_below"]


def newest_release(tags, rule):
    """The highest tag that is a release under `rule`; None without a rule or
    a release."""
    if rule is None:
        return None
    releases = [(series(t)[0], t) for t in tags or [] if is_release(t, rule)]
    return max(releases)[1] if releases else None


def newest_in_series(tag, tags):
    """The highest tag with `tag`'s suffix and as many version parts, or None
    when `tag` has no version. `2.4.537-MOB-...-reduced` is another series
    than `2.4.533-reduced`."""
    mine = series(tag)
    if mine is None:
        return None
    version, suffix = mine
    best = (version, tag)
    for t in tags or []:
        s = series(t)
        if s and s[1] == suffix and len(s[0]) == len(version) and s[0] > best[0]:
            best = (s[0], t)
    return best[1]


def registry_for(ref, credentials=None, ca_file=None):
    """(Registry, path, tag) for an image reference."""
    scheme, host, path, tag = split_ref(ref)
    return Registry(host, scheme=scheme, credentials=credentials,
                    ca_file=ca_file), path, tag


def newest_versions(tags, limit=RESOLVE_CANDIDATES):
    """The `limit` newest versioned tags, highest version first; at one
    version the shortest suffix first (`2.4.538-reduced` before a branch
    build of 2.4.538)."""
    versioned = [(series(t), t) for t in tags or []]
    versioned = [(s[0], len(s[1]), s[1], t) for s, t in versioned if s]
    versioned.sort(key=lambda v: (tuple(-p for p in v[0]), v[1], v[2]))
    return [v[3] for v in versioned[:limit]]


def resolve(reg, path, digest, tags, budget_s=None):
    """(versioned tag whose manifest digest is `digest`, detail). Compares the
    top-level digest, so a multi-arch index matches its own tag. The tag is
    None with a detail when nothing matched or a comparison went unread."""
    candidates = newest_versions(tags)
    if not candidates:
        return None, "the repository has no versioned tag to compare with"
    budget_s = RESOLVE_BUDGET_S if budget_s is None else budget_s
    deadline = time.monotonic() + budget_s
    unread = []
    for n, cand in enumerate(candidates):
        if time.monotonic() > deadline:
            return None, (f"stopped comparing after {budget_s}s; "
                          f"{len(candidates) - n} tags not compared")
        c = reg.check(path, cand)
        if c["state"] == PRESENT and c["digest"] == digest:
            return cand, None
        if c["state"] == UNREAD:
            unread.append(f"{cand}: {c['detail']}")
    if unread:
        return None, (f"{len(unread)} of {len(candidates)} newest versioned "
                      f"tags could not be compared ({unread[0]})")
    # Measured on BlazeMeter's registry: `latest` can be far older than the
    # newest releases, so saying which range was compared is the useful part.
    return None, (f"it is none of the {len(candidates)} newest versioned tags "
                  f"({candidates[0]} down to {candidates[-1]})")


def _newer(newest, current):
    """Is `newest` a higher version than `current`? None when they do not
    compare (no version, or a different number of parts)."""
    a, b = series(newest), series(current)
    if not a or not b or len(a[0]) != len(b[0]):
        return None
    return a[0] > b[0]


def lookup(ref, release_rule=None):
    """What the registry says about one public image: {registry_state,
    registry_detail, digest, size_mb, newest_tag, update_available,
    resolves_to}.

    `registry_state` is read when the manifest read answered, 404 included. A
    tag list that could not be read leaves newest_tag None and says why. A
    floating tag (`latest`) is named by the versioned tag sharing its digest
    (`resolves_to`); no match and an unread comparison both leave it None,
    and `registry_detail` says which.

    With `release_rule` (the repository's RELEASE_SERIES entry) the newest
    tag is the newest release under that rule, the one a bundle made without
    an account pins, so a CI build is never offered as an update.
    """
    reg, path, tag = registry_for(ref)
    m = reg.manifest(path, tag)
    out = {"registry_state": m["state"], "registry_detail": m["detail"],
           "digest": m["digest"], "size_mb": m["size_mb"],
           "newest_tag": None, "update_available": None, "resolves_to": None}

    def note(why):
        out["registry_detail"] = f"{out['registry_detail']}; {why}" \
            if out["registry_detail"] else why

    if m["state"] != READ or not m["found"]:
        return out
    floating = series(tag) is None
    if floating and not m["digest"]:
        note("the registry sent no digest, so the tag cannot be named")
        return out
    t = reg.tags(path)
    if t["state"] != READ:
        note(f"the tag list could not be read: {t['detail']}")
        return out
    current = tag
    if floating:
        current, why = resolve(reg, path, m["digest"], t["tags"])
        if current is None:
            note(f"{tag} could not be named: {why}")
            return out
        out["resolves_to"] = current
    if release_rule is not None:
        newest = newest_release(t["tags"], release_rule)
        out["newest_tag"] = newest
        out["update_available"] = None if newest is None else _newer(newest, current)
        return out
    newest = newest_in_series(current, t["tags"])
    out["newest_tag"] = newest
    out["update_available"] = None if newest is None else newest != current
    return out
