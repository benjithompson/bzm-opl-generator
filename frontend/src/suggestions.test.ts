import { describe, expect, it } from "vitest";
import { MergeState, Options, Strength, Suggestion } from "./api";
import { allGroupsOff, detectGroups } from "./optionGroups";
import {
  apply, applyPatch, canUndo, clipValue, NOTHING_APPLIED, offer, record,
  STRENGTH_STYLE, suggestionLine, undo,
} from "./suggestions";

// What the panel offers, and what applying one does to the configuration. The
// suggestions themselves are suggest.py's and are tested against the command in
// tests/test_suggest.py; how each one stands against the options is suggest.py's
// too (SETTLED | FILL | CHOOSE | CONFLICT), and arrives on the row -- as does
// why a row cannot be offered, and every value it displays. Nothing here
// re-decides any of it: what is decided here is what a user may click, and what
// clicking it writes.

const sugg = (over: Partial<Suggestion> = {}): Suggestion => ({
  option: "platform", strength: "DECISIVE" as Strength, value: "k8s",
  candidates: ["k8s"], ruled_out: [], evidence: ["api_groups.openshift_security"],
  detail: "security.openshift.io is not served, so this is plain Kubernetes",
  state: "FILL" as MergeState, current: "openshift",
  current_shown: "openshift", value_shown: "k8s", candidates_shown: ["k8s"],
  ruled_out_shown: [], blocked: null,
  ...over,
});

const shortlist = (over: Partial<Suggestion> = {}): Suggestion => sugg({
  option: "sv_ingress", strength: "SUGGESTIVE", value: null,
  candidates: ["nginx", "contour"], ruled_out: ["istio"],
  evidence: ["api_groups.istio", "api_groups.contour"],
  state: "CHOOSE", current: null,
  current_shown: "not set", value_shown: "not set",
  candidates_shown: ["nginx", "contour"], ruled_out_shown: ["istio"],
  ...over,
});

describe("what a row offers", () => {
  it("offers a decisive suggestion nothing has moved as a one-click default", () => {
    // The value applying writes, and the string the button wears -- the second
    // straight off the row, never formatted here.
    expect(offer(sugg())).toEqual({ kind: "apply", value: "k8s", shown: "k8s" });
  });

  it("offers nothing where the configuration already says it", () => {
    // The one state in which "the cluster confirms this" is truthful, and
    // writing it back would re-render an identical bundle.
    expect(offer(sugg({ state: "SETTLED", current: "k8s" })))
      .toEqual({ kind: "none" });
  });

  it("makes a disagreement a replace, never an apply", () => {
    // Same click count, deliberately different word: what it does is overwrite
    // a value somebody chose, and the row shows both before it happens.
    const s = sugg({ option: "pull_secret", value: "regcred",
      candidates: ["regcred"], state: "CONFLICT", current: "team-creds",
      current_shown: "team-creds", value_shown: "regcred",
      candidates_shown: ["regcred"] });
    expect(offer(s))
      .toEqual({ kind: "replace", value: "regcred", shown: "regcred" });
  });

  it("never hands back a value for a suggestive suggestion", () => {
    // The invariant the whole feature rests on. A shortlist of one is still a
    // shortlist: narrowing to one is not choosing, so there is no value here
    // for a caller to quietly promote into a default.
    for (const state of ["CHOOSE", "CONFLICT"] as MergeState[]) {
      for (const candidates of [["contour"], ["nginx", "contour"]]) {
        const o = offer(shortlist({ state, candidates,
          candidates_shown: candidates }));
        expect(o).toEqual({ kind: "choose",
          candidates: candidates.map((c) => ({ value: c, shown: c })) });
        expect(o).not.toHaveProperty("value");
      }
    }
  });

  it("offers nothing when the evidence ruled every candidate out", () => {
    // sv_ingress with an empty shortlist is a finding, not an action: no
    // controller this cluster serves can publish a virtual service.
    expect(offer(shortlist({ candidates: [], candidates_shown: [],
      ruled_out: ["nginx", "istio"] }))).toEqual({ kind: "none" });
  });

  it("states the served refusal rather than deciding there is one", () => {
    // generate() takes exactly one CA mode of the three, so applying this one
    // would need the other cleared -- and clearing it is precisely the silent
    // overwrite this feature may not make. Which modes are one-of, and what to
    // say about it, is generate's; the row arrives carrying the sentence, and
    // these options hold no CA mode at all.
    const because = "custom CA trust already uses an inline PEM — clear it "
      + "first, because a bundle carrying two CA modes does not generate";
    const s = shortlist({ option: "ca_openshift_inject", candidates: [true],
      candidates_shown: ["true"], state: "CHOOSE", current: false,
      current_shown: "false", blocked: because });
    expect(offer(s)).toEqual({ kind: "blocked", because });
    // ...and the same suggestion is offerable when nothing was served.
    expect(offer({ ...s, blocked: null }).kind).toBe("choose");
  });

  it("keeps a settled row silent even where something is blocked", () => {
    // Nothing to apply is nothing to refuse: the row already holds what the
    // evidence says, so the refusal would be an explanation for a button that
    // was never going to be there.
    expect(offer(sugg({ state: "SETTLED", current: "k8s",
      blocked: "custom CA trust already uses an inline PEM" })))
      .toEqual({ kind: "none" });
  });
});

describe("applying one", () => {
  it("writes the option and nothing else", () => {
    // The seam that makes the feature honest: an applied value has to be
    // indistinguishable from a typed one downstream, so the patch is the option
    // and its value -- no marker, no provenance, nothing for generate() to see.
    expect(applyPatch("pull_secret", "regcred")).toEqual({ pull_secret: "regcred" });
    expect(Object.keys(applyPatch("cluster_rbac", false))).toEqual(["cluster_rbac"]);
  });

  it("remembers the value the row displayed, not one read back out of the options", () => {
    // The two are not the same value. `current` is the server's, and it falls
    // back to the generator's default for an option nobody set; the options
    // object may simply not carry the key. Reading the previous value from
    // there gave "Undo → not set" on a row that said "now openshift", and
    // clicking it wrote an explicit null instead of putting the default back.
    // One source for that value, and it is the one on screen.
    const s = sugg();                          // platform: current "openshift"
    const { patch, applied } = apply(NOTHING_APPLIED, s, "k8s");
    expect(patch).toEqual({ platform: "k8s" });
    expect(applied.platform.previous).toBe("openshift");
    expect(undo(applied, "platform")?.patch).toEqual({ platform: "openshift" });
  });

  it("keeps the first previous value when a second candidate is picked", () => {
    // Same rule `record` keeps, through the seam the panel actually uses: what
    // a person wants back is the configuration they had before the panel
    // touched anything.
    const s = shortlist();                     // sv_ingress: current null
    const once = apply(NOTHING_APPLIED, s, "contour").applied;
    const twice = apply(once, { ...s, current: "contour" }, "nginx").applied;
    expect(undo(twice, "sv_ingress")?.patch).toEqual({ sv_ingress: null });
  });

  it("leaves the option groups agreeing with what it wrote", () => {
    // A group is detected from the options it owns, so applying into one that
    // is switched off has to open it -- otherwise the value ships from a panel
    // that shows nothing set.
    const applied: Options = { ...applyPatch("pull_secret", "regcred") };
    expect(detectGroups(applied, allGroupsOff()).registry).toBe(true);
    expect(detectGroups({ ...applyPatch("sv_ingress", "contour") },
      allGroupsOff()).sv).toBe(true);
  });
});

describe("taking it back", () => {
  const first = record(NOTHING_APPLIED, "sv_ingress",
                       { value: null, shown: "not set" }, "contour");

  it("restores the value that was there, without it being re-entered", () => {
    const back = undo(record(NOTHING_APPLIED, "pull_secret",
      { value: "team-creds", shown: "team-creds" }, "regcred"), "pull_secret");
    expect(back?.patch).toEqual({ pull_secret: "team-creds" });
    // And the record goes with it: undone is not applied.
    expect(back?.applied).toEqual(NOTHING_APPLIED);
  });

  it("remembers how that value was shown, because nothing serves it twice", () => {
    // The undo button names a value the browser alone still holds: the row it
    // came from has been re-rendered against the configuration as it now is.
    // So the string the server wrote is kept beside it rather than composed
    // here from the value.
    expect(first.sv_ingress.previousShown).toBe("not set");
  });

  it("restores an option that held nothing, rather than the default", () => {
    // null is what the option held, and what the group's own disable writes.
    expect(undo(first, "sv_ingress")?.patch).toEqual({ sv_ingress: null });
  });

  it("goes back to what was there before the panel touched it", () => {
    // Picking a second candidate off the same shortlist must not make the first
    // one the thing undo returns to.
    const second = record(first, "sv_ingress",
                          { value: "contour", shown: "contour" }, "nginx");
    expect(undo(second, "sv_ingress")?.patch).toEqual({ sv_ingress: null });
    expect(second.sv_ingress.value).toBe("nginx");
    // ...and the label goes back with it.
    expect(second.sv_ingress.previousShown).toBe("not set");
  });

  it("has nothing to undo for an option it never wrote", () => {
    expect(undo(NOTHING_APPLIED, "platform")).toBeNull();
    expect(undo(first, "platform")).toBeNull();
  });

  it("stops offering the undo once the value has been typed over", () => {
    // Undo restores what was there BEFORE the panel wrote, so offering it over
    // a value somebody typed afterwards would overwrite that -- the one thing
    // this feature may not do. Ours to take back only while it is still ours.
    expect(canUndo(first, "sv_ingress", { sv_ingress: "contour" })).toBe(true);
    expect(canUndo(first, "sv_ingress", { sv_ingress: "nginx" })).toBe(false);
    expect(canUndo(NOTHING_APPLIED, "sv_ingress", { sv_ingress: "contour" }))
      .toBe(false);
    // Compared by value, not identity: the proxy suggestion applies an object.
    const proxied = record(NOTHING_APPLIED, "proxy",
                           { value: null, shown: "not set" },
                           { https: "http://p:3128" });
    expect(canUndo(proxied, "proxy", { proxy: { https: "http://p:3128" } })).toBe(true);
  });
});

describe("reading the list", () => {
  it("tells the two strengths apart in words as well as colour", () => {
    // Same rule as the verdict badges: this gets read on a projector, and a
    // shortlist that is only a different shade of grey is a default to anyone
    // who cannot see the difference.
    const s: Strength[] = ["DECISIVE", "SUGGESTIVE"];
    expect(new Set(s.map((x) => STRENGTH_STYLE[x].label)).size).toBe(2);
    expect(new Set(s.map((x) => STRENGTH_STYLE[x].badge)).size).toBe(2);
  });

  it("counts what is on offer, in the states' own terms", () => {
    const line = suggestionLine([
      sugg(), sugg({ option: "cluster_rbac", state: "SETTLED" }),
      sugg({ option: "pull_secret", state: "CONFLICT" }), shortlist(),
    ]);
    expect(line).toContain("1 to apply");
    expect(line).toContain("1 shortlist");
    expect(line).toContain("1 disagreement");
    expect(line).toContain("1 already");
  });

  it("says nothing about options this evidence never mentioned", () => {
    // Silence is the whole claim: an option no suggestion names was not
    // checked, and the summary must not imply the configuration was.
    expect(suggestionLine([])).toBe("");
  });

  it("cuts a served value down to what a button can hold, and only there", () => {
    // A proxy configuration is three URLs in one object; a label that long
    // decides the width of the whole panel. The row still shows it in full,
    // and how it reads at all was decided before it got here -- this only
    // shortens it.
    const proxy = '{"http": "http://proxy.corp:3128", '
      + '"https": "http://proxy.corp:3128"}';
    expect(clipValue(proxy).length).toBeLessThanOrEqual(22);
    expect(clipValue(proxy)).toContain("…");
    expect(clipValue("nginx")).toBe("nginx");
    expect(clipValue("not set")).toBe("not set");
  });
});
