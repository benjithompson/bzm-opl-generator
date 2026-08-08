import { describe, expect, it } from "vitest";
import { MARKER_EXAMPLES } from "./fixtures";
import { GroupFlags } from "./optionGroups";
import {
  blankRequired, marker, placeholderWarning, withPlaceholders,
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
    // What a form hands back when somebody selects a value and hits space.
    expect(blankRequired({ namespace: "  ", service_account_name: "crane" },
                         k8s, off)).toEqual(["namespace"]);
  });

  it("never names a field this format does not have", () => {
    // Inherited from configureBlockedBy, where it was the reported bug: a
    // docker bundle has no namespace and no ServiceAccount, so naming them
    // points at two boxes that are deliberately not on screen. A warning can
    // do that just as badly as a blocker could.
    expect(blankRequired({}, docker, off)).toEqual([]);
  });

  it("asks the predicate rather than trusting a filled-in field", () => {
    // A docker bundle keeps its namespace in the options -- the value is kept,
    // not wiped -- so "is it filled in" cannot answer "is it a field here".
    expect(blankRequired({ namespace: "" }, docker, off)).toEqual([]);
  });
});

// -- the half the server cannot work out for itself ---------------------------
// A registry, a proxy and a CA are configured by *having a value*, so on the
// server "blank" and "not using one" are the same options dict. The switch is
// here, and this is the case that used to pass silently: group on, field empty,
// bundle generated with no private registry and nothing anywhere saying so.

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
    // Either one is a working configuration, so neither is then missing.
    expect(blankRequired({ ...filled, proxy: { http: "http://p:3128" } },
                         k8s, on("proxy"))).toEqual([]);
  });

  it("asks for what the chosen CA mode needs, and only that", () => {
    expect(blankRequired({ ...filled, ca_existing_configmap: "" },
                         k8s, on("ca"))).toEqual(["ca_existing_configmap"]);
    expect(blankRequired({ ...filled, ca_bundle: "" }, k8s, on("ca")))
      .toEqual(["ca_bundle"]);
    // OpenShift injection fills a ConfigMap this bundle names itself, so there
    // is nothing for anybody to type.
    expect(blankRequired({ ...filled, ca_openshift_inject: true },
                         k8s, on("ca"))).toEqual([]);
  });

  it("asks for the SV fields only once a backend is chosen", () => {
    expect(blankRequired({ ...filled, sv_ingress: "nginx" }, k8s, on("sv")))
      .toEqual(["sv_subdomain", "sv_tls_secret"]);
    // Nobody has picked one yet: that is a question, not an empty box, and it
    // is configureBlockedBy's to refuse.
    expect(blankRequired(filled, k8s, on("sv"))).toEqual([]);
  });
});

describe("marker", () => {
  it("follows the rule the generator follows", () => {
    // The examples are in fixtures.ts, and test_server.py asserts
    // generate.marker against the same entries -- so this side and that side
    // cannot drift apart without one of the two failing.
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
    // The marker is the whole dotted key's, not the sub-key's: the generator
    // reports `proxy.https` back and the two have to be one name for one field.
    const o = { proxy: { no_proxy: "localhost" } };
    expect(withPlaceholders(o, ["proxy.https"]).proxy)
      .toEqual({ no_proxy: "localhost", https: "<PROXY_HTTPS>" });
  });

  it("returns the same object when there is nothing to fill", () => {
    // Identity, not equality: the preview effect keys on it, and a fresh object
    // every render would re-POST /api/generate for a bundle nobody changed.
    const o = { namespace: "ns" };
    expect(withPlaceholders(o, [])).toBe(o);
  });

  it("does not touch the options it was given", () => {
    // The marker must not reach the page's own state, or it lands in the
    // session snapshot and comes back looking like something somebody typed.
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
    // Paired with the field rather than listed after it: a reader who greps
    // the bundle for <PROXY_HTTPS> must not have to line two lists up by
    // position to learn which box that came from.
    const w = placeholderWarning(["proxy.http", "proxy.https"]);
    expect(w).toContain("proxy.http (<PROXY_HTTP>)");
    expect(w).toContain("proxy.https (<PROXY_HTTPS>)");
  });
});
