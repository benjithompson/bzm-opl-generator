#!/usr/bin/env bash
#
# bzm-cluster-evidence — collect the cluster facts a BlazeMeter private-location
# (OPL) deployment has to be shaped around, into one JSON file you can send back.
#
#   ./bzm-cluster-evidence.sh -n <namespace> > cluster-evidence.json
#
# WHAT THIS DOES, so a security reviewer can confirm it in one pass:
#
#   * It is READ-ONLY. Every command below is `get`, `api-resources`, `version`
#     or `auth can-i`. There is no create, apply, patch, delete or exec, and
#     nothing is sent anywhere -- output goes to stdout and nowhere else.
#     `grep -nE '(create|apply|patch|delete|exec)' this-file` returns only the
#     `auth can-i` permission probes, which ask *whether* you could, and do not.
#
#   * NO SECRET VALUES ARE READ. Secrets are listed by name and type only
#     (`-o custom-columns`), never `-o json`, so no secret data is ever in the
#     output. ConfigMaps are listed by name only for the same reason -- and
#     because a CA-bundle ConfigMap is ~300KB that nobody needs here.
#
#   * It works without cluster-admin, and without the namespace existing yet.
#     Anything you cannot read is recorded as `null` with the error, which is a
#     genuinely different answer from "read it, there were none" -- the tool
#     that consumes this treats them differently and must not confuse them.
#
# Requires kubectl (or oc) already pointed at the right cluster. No jq needed.

set -u

NS=""
CLI=""

while [ $# -gt 0 ]; do
    case "$1" in
        -n|--namespace) NS="${2:-}"; shift 2 ;;
        --cli)          CLI="${2:-}"; shift 2 ;;
        -h|--help)
            sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done

if [ -z "$CLI" ]; then
    if command -v oc  >/dev/null 2>&1; then CLI=oc
    elif command -v kubectl >/dev/null 2>&1; then CLI=kubectl
    else echo "neither oc nor kubectl found on PATH" >&2; exit 1
    fi
fi

if [ -z "$NS" ]; then
    echo "usage: $0 -n <namespace>   (the namespace the agent will run in," >&2
    echo "                            whether or not it exists yet)" >&2
    exit 2
fi

# Collected as we go; emitted at the end so the reader sees what failed.
NOTES=""
note() { NOTES="${NOTES}${NOTES:+,}$(json_str "$1")"; }

# Only ever applied to command output and k8s object names, never to secret
# data. Escapes backslash, quote and the control chars JSON forbids raw.
json_str() {
    printf '%s' "$1" | LC_ALL=C sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
        -e 's/\r/\\r/g' -e "s/$(printf '\t')/\\\\t/g" \
        | awk 'BEGIN{ORS=""; print "\""} {print (NR>1 ? "\\n" : "") $0} END{print "\""}'
}

# `get -o json` verbatim, or JSON null when the command failed. Emitting the
# untouched kubectl document matters twice over: a reviewer can see nothing was
# rewritten on the way out, and normalising it is the importer's job, where it
# is testable. null vs an empty list is the load-bearing distinction -- "we were
# denied" must not arrive looking like "the cluster has none".
get_json() {
    key="$1"; shift
    out=$("$CLI" get "$@" -o json 2>/tmp/bzm-ev-err.$$)
    if [ $? -eq 0 ] && [ -n "$out" ]; then
        printf '    "%s": %s' "$key" "$out"
    else
        note "$key: $(head -c 300 /tmp/bzm-ev-err.$$ | tr '\n' ' ')"
        printf '    "%s": null' "$key"
    fi
    rm -f /tmp/bzm-ev-err.$$
}

# A JSON array of names. Safe to build in shell precisely because Kubernetes
# object names are DNS labels -- lowercase alphanumerics, '-' and '.' only, so
# there is nothing here that needs quoting.
get_names() {
    key="$1"; shift
    # Same note-on-failure as get_json: a null with no note is a section that
    # silently disappears from "what could not be read", and the reader then
    # presents a partial file as a complete one.
    out=$("$CLI" get "$@" -o custom-columns=N:.metadata.name --no-headers 2>/tmp/bzm-ev-err.$$)
    if [ $? -ne 0 ]; then
        note "$key: $(head -c 300 /tmp/bzm-ev-err.$$ | tr '\n' ' ')"
        rm -f /tmp/bzm-ev-err.$$
        printf '    "%s": null' "$key"; return
    fi
    rm -f /tmp/bzm-ev-err.$$
    printf '    "%s": [' "$key"
    first=1
    for n in $out; do
        [ "$n" = "<none>" ] && continue
        [ $first -eq 1 ] || printf ', '
        printf '"%s"' "$n"; first=0
    done
    printf ']'
}

# yes/no, as a JSON boolean. `auth can-i` asks the API server what it would
# allow; it performs nothing.
can_i() {
    label="$1"; verb="$2"; res="$3"; scope="${4:-ns}"
    if [ "$scope" = "cluster" ]; then
        ans=$("$CLI" auth can-i "$verb" "$res" 2>/dev/null)
    else
        ans=$("$CLI" auth can-i "$verb" "$res" -n "$NS" 2>/dev/null)
    fi
    [ "$ans" = "yes" ] && v=true || v=false
    printf '      "%s": %s' "$label" "$v"
}

has_api() {
    "$CLI" api-resources --api-group="$1" -o name 2>/dev/null | grep -q . && echo true || echo false
}

# ---------------------------------------------------------------------------

printf '{\n'
printf '  "schema": "bzm-opl-cluster-evidence/1",\n'
printf '  "collected_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '  "namespace": "%s",\n' "$NS"
printf '  "cli": "%s",\n' "$CLI"

# -- raw objects: exactly what doctor's checks read -------------------------
printf '  "raw": {\n'
get_json nodes          nodes                                   ; printf ',\n'
get_json ingressclasses ingressclass                            ; printf ',\n'
get_json namespace      ns "$NS"                                ; printf ',\n'
get_json scoped         limitrange,resourcequota,serviceaccount -n "$NS"
printf '\n  },\n'

# -- names only: never the contents -----------------------------------------
printf '  "inventory": {\n'
get_names configmaps configmap -n "$NS" ; printf ',\n'
# Type is as far as this goes: it is what identifies an imagePullSecret
# (kubernetes.io/dockerconfigjson) without reading anything inside one.
sec=$("$CLI" get secret -n "$NS" -o custom-columns=N:.metadata.name,T:.type --no-headers 2>/tmp/bzm-ev-err.$$)
if [ $? -eq 0 ]; then
    printf '    "secrets": ['
    first=1
    # Fed by here-doc, not a pipe: a `while` on the right of a pipe runs in a
    # subshell, so `first` would reset every iteration and every entry would be
    # emitted with a leading comma.
    while read -r n t _; do
        [ -z "$n" ] && continue
        [ $first -eq 1 ] || printf ', '
        printf '{"name":"%s","type":"%s"}' "$n" "$t"; first=0
    done <<EOF
$sec
EOF
    printf ']'
else
    note "secrets: $(head -c 300 /tmp/bzm-ev-err.$$ | tr '\n' ' ')"
    printf '    "secrets": null'
fi
rm -f /tmp/bzm-ev-err.$$
printf '\n  },\n'

# -- what the API server says you may do ------------------------------------
# This is the part `doctor` cannot ask on your behalf: whether the bundle will
# even apply. A namespaced Role is all a standard deployment needs; the
# cluster-scoped rows decide only whether the bundle's *optional* ClusterRole
# can be applied. They say nothing about serviceType: crane resolves its
# advertised address from its own network interfaces, not from the Node object,
# and NODEPORT has run green with namespaced RBAC only (issue #49).
#
# Note for whoever reads this file: a `false` below is "the API server said no"
# only when the command reached it -- `auth can-i` and `api-resources` both
# report failure as no. `versions.serverVersion` is what tells the two apart,
# and bzm_opl_gen/suggest.py will not suggest anything without it.
printf '  "permissions": {\n'
printf '    "namespaced": {\n'
can_i "create serviceaccounts" create serviceaccounts ; printf ',\n'
can_i "create roles"           create roles           ; printf ',\n'
can_i "create rolebindings"    create rolebindings    ; printf ',\n'
can_i "create configmaps"      create configmaps      ; printf ',\n'
can_i "create secrets"         create secrets         ; printf ',\n'
can_i "create deployments"     create deployments.apps; printf ',\n'
can_i "create ingresses"       create ingresses.networking.k8s.io
printf '\n    },\n'
printf '    "cluster_scoped": {\n'
can_i "list nodes"              list   nodes               cluster ; printf ',\n'
can_i "create clusterroles"     create clusterroles        cluster ; printf ',\n'
can_i "create clusterrolebindings" create clusterrolebindings cluster
printf '\n    }\n  },\n'

# -- which ingress backends this cluster could actually serve ---------------
# The generator needs exactly one, and picking one the cluster does not run is
# the failure that shows up as a virtual service stalled at WAITING_FOR_DOMAIN.
printf '  "api_groups": {\n'
printf '    "openshift_route": %s,\n'  "$(has_api route.openshift.io)"
printf '    "openshift_security": %s,\n' "$(has_api security.openshift.io)"
printf '    "istio": %s,\n'            "$(has_api networking.istio.io)"
printf '    "contour": %s\n'           "$(has_api projectcontour.io)"
printf '  },\n'

# -- OpenShift cluster-level config, when present ---------------------------
# The ingress domain is what --sv-subdomain has to match, and the cluster proxy
# is the customer's real proxy posture rather than one you have to ask about.
printf '  "openshift": {\n'
get_json ingress_config ingresses.config.openshift.io cluster ; printf ',\n'
get_json proxy_config   proxies.config.openshift.io cluster
printf '\n  },\n'

printf '  "versions": '
# Not `get`, so it does not go through get_json.
ver=$("$CLI" version -o json 2>/dev/null)
if [ -n "$ver" ]; then printf '%s' "$ver"; else printf 'null'; fi
printf ',\n'

printf '  "notes": [%s]\n' "$NOTES"
printf '}\n'
