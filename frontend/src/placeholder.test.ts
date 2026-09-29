import { describe, expect, it } from "vitest";
import { MARKER_EXAMPLES } from "./fixtures";
import { GroupFlags } from "./optionGroups";
import {
  blankRequired, gapSummary, gaps, marker, placeholderWarning,
  withPlaceholders,
} from "./placeholder";

/** Every option applies: a Kubernetes bundle. */
const k8s = () => true;
/** ...and one where the placement fields are not fields at all. */
const docker = (k: string) =>
  !["namespace", "service_account_name", "service_account_create"].includes(k);

const off = {} as GroupFlags;
const on = (...ids: string[]) =>
  Object.fromEntries(ids.map((i) => [i, true])) as GroupFlags;

const filled = { namespace: "blazemeter", service_account_name: "crane" };

describe("blankRequired", () => {
  it("finds nothing on a configuration that is filled in", () => {
    expect(blankRequired(filled, k8s, off)).toEqual([]);
  });

  it("names the placement fields a cluster bundle is missing", () => {
    expect(blankRequired({}, k8s, off))
      .toEqual(["namespace", "service_account_name"]);
    expect(blankRequired({ namespace: "ns" }, k8s, off))
      .toEqual(["service_account_name"]);
  });

  it("reads whitespace as empty", () => {
    // Whitespace only is blank.
    expect(blankRequired({ namespace: "  ", service_account_name: "crane" },
                         k8s, off)).toEqual(["namespace"]);
  });

  it("never names a field this format does not have", () => {
    // A docker bundle has no namespace or ServiceAccount field to name.
    expect(blankRequired({}, docker, off)).toEqual([]);
  });

  it("asks the predicate rather than trusting a filled-in field", () => {
    // ...even when the options still hold a value.
    expect(blankRequired({ namespace: "" }, docker, off)).toEqual([]);
  });
});

// -- what only a switch on the page shows --------------------------------------
// A group switched on with its field empty: the server cannot tell it from
// "not using one".

describe("blankRequired, for a group that is switched on", () => {
  it("finds a registry with no host", () => {
    expect(blankRequired(filled, k8s, on("registry")))
      .toEqual(["private_registry"]);
  });

  it("says nothing about a group that is off", () => {
    expect(blankRequired(filled, k8s, off)).toEqual([]);
  });

  it("asks for one proxy URL, not both", () => {
    expect(blankRequired(filled, k8s, on("proxy"))).toEqual(["proxy.https"]);
    // Either URL is a working proxy.
    expect(blankRequired({ ...filled, proxy: { http: "http://p:3128" } },
                         k8s, on("proxy"))).toEqual([]);
  });

  it("asks for what the chosen CA mode needs, and only that", () => {
    expect(blankRequired({ ...filled, ca_existing_configmap: "" },
                         k8s, on("ca"))).toEqual(["ca_existing_configmap"]);
    expect(blankRequired({ ...filled, ca_bundle: "" }, k8s, on("ca")))
      .toEqual(["ca_bundle"]);
    // Injection fills a ConfigMap the bundle names: nothing to type.
    expect(blankRequired({ ...filled, ca_openshift_inject: true },
                         k8s, on("ca"))).toEqual([]);
  });

  it("asks for the SV fields only once a backend is chosen", () => {
    expect(blankRequired({ ...filled, sv_ingress: "nginx" }, k8s, on("sv")))
      .toEqual(["sv_subdomain", "sv_tls_secret"]);
    // No ingress chosen is a question, not an empty box.
    expect(blankRequired(filled, k8s, on("sv"))).toEqual([]);
  });
});

describe("marker", () => {
  it("follows the rule the generator follows", () => {
    // The examples test_server.py also holds generate.marker to.
    for (const [key, want] of Object.entries(MARKER_EXAMPLES)) {
      expect(marker(key)).toBe(want);
    }
  });
});

describe("withPlaceholders", () => {
  it("fills exactly what it was given", () => {
    const o = { namespace: "", service_account_name: "crane" };
    expect(withPlaceholders(o, ["namespace"]))
      .toEqual({ namespace: "<NAMESPACE>", service_account_name: "crane" });
  });

  it("reaches into a nested key", () => {
    // The marker is the whole dotted key's.
    const o = { proxy: { no_proxy: "localhost" } };
    expect(withPlaceholders(o, ["proxy.https"]).proxy)
      .toEqual({ no_proxy: "localhost", https: "<PROXY_HTTPS>" });
  });

  it("returns the same object when there is nothing to fill", () => {
    // Same object when nothing is filled, so the preview does not re-POST.
    const o = { namespace: "ns" };
    expect(withPlaceholders(o, [])).toBe(o);
  });

  it("does not touch the options it was given", () => {
    // The marker never reaches the page's own options.
    const o = { namespace: "", proxy: { no_proxy: "localhost" } };
    withPlaceholders(o, ["namespace", "proxy.https"]);
    expect(o).toEqual({ namespace: "", proxy: { no_proxy: "localhost" } });
  });
});

describe("placeholderWarning", () => {
  it("says nothing when nothing is blank", () => {
    expect(placeholderWarning([])).toBe("");
  });

  it("agrees with itself about number", () => {
    const one = placeholderWarning(["namespace"]);
    expect(one).toContain("namespace (<NAMESPACE>) is empty");
    expect(one).toContain("until it is filled in");
    const two = placeholderWarning(["namespace", "service_account_name"]);
    expect(two).toContain("namespace (<NAMESPACE>) and "
      + "service_account_name (<SERVICE_ACCOUNT_NAME>) are empty");
    expect(two).toContain("until they are filled in");
  });

  it("names each field's own marker, so the sentence and the file are one "
     + "search", () => {
    // Each field paired with its marker.
    const w = placeholderWarning(["proxy.http", "proxy.https"]);
    expect(w).toContain("proxy.http (<PROXY_HTTP>)");
    expect(w).toContain("proxy.https (<PROXY_HTTPS>)");
  });
});

describe("gaps", () => {
  it("orders the list the way somebody fills it in", () => {
    const list = gaps(["harbor_id", "ship_id"], ["namespace"], true, null);
    expect(list.map((g) => g.key))
      .toEqual(["harbor_id", "ship_id", "auth_token", "namespace"]);
  });

  it("sends the identity and the credential back to step 1, and the options "
     + "to step 2", () => {
    const list = gaps(["harbor_id"], ["sv_subdomain"], true, null);
    expect(Object.fromEntries(list.map((g) => [g.key, g.step])))
      .toEqual({ harbor_id: 1, auth_token: 1, sv_subdomain: 2 });
  });

  // Before the first preview the token is unread, not blank.
  it("does not list the token before a preview has answered", () => {
    expect(gaps([], [], "unread", null).map((g) => g.key)).toEqual([]);
  });

  it("...and does not list one the preview says is there", () => {
    expect(gaps([], [], false, null).map((g) => g.key)).toEqual([]);
  });

  it("carries the marker with no table read at all", () => {
    const [gap] = gaps([], ["proxy.https"], false, null);
    expect(gap.marker).toBe("<PROXY_HTTPS>");
    // Absent, not "", when unread.
    expect("source" in gap).toBe(false);
  });

  it("takes the sentence from the served table where there is one", () => {
    const [ns, sa] = gaps([], ["namespace", "service_account_name"], false,
                          { namespace: { marker: "<NAMESPACE>",
                                         source: "your platform team" } });
    expect(ns.source).toBe("your platform team");
    // ...per key: one entry arriving does not make the rest look answered.
    expect("source" in sa).toBe(false);
  });
});

describe("gapSummary", () => {
  const of = (...keys: string[]) => gaps([], keys, false, null);

  it("names them all while they fit", () => {
    expect(gapSummary(of("namespace"))).toBe("<NAMESPACE>");
    expect(gapSummary(of("namespace", "auth_token")))
      .toBe("<NAMESPACE> and <AUTH_TOKEN>");
  });

  it("counts the tail rather than truncating it", () => {
    // Counted, not truncated.
    const s = gapSummary(of("a", "b", "c", "d", "e"));
    expect(s).toBe("<A>, <B> and 3 more");
  });

  it("says nothing about an empty list", () => {
    expect(gapSummary([])).toBe("");
  });
});
