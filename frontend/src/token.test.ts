import { describe, expect, it } from "vitest";
import { TokenReport } from "./api";
import { downloadPlan, recallNote, recalled, rotateHazard } from "./token";

const report = (branch: TokenReport["branch"]): TokenReport =>
  ({ branch, ship_id: "bbb222", message: "…" });

describe("downloadPlan", () => {
  it("never rotates, which is the whole of #64", () => {
    for (const b of ["given", "reused", "placeholder"] as const) {
      // The request itself, whole: what a caller sends is what this returned,
      // so there is no boolean left anywhere for a button to re-apply.
      expect(downloadPlan(report(b)).request).toEqual({ rotate_token: false });
    }
    // ...including before the first preview, when there is no report at all.
    expect(downloadPlan(null).request).toEqual({ rotate_token: false });
  });

  it("says a bundle with no token cannot be applied yet", () => {
    const plan = downloadPlan(report("placeholder"));
    expect(plan.incomplete).toBe(true);
    expect(plan.hint).toMatch(/fill it in/);
  });

  // Before the first preview lands there is nothing to go on, and the honest
  // answer is the one that understates the bundle rather than the one that
  // claims a token is in it.
  it("assumes the placeholder before anything has been generated", () => {
    expect(downloadPlan(null).incomplete).toBe(true);
  });

  it("treats a token in the form as complete", () => {
    const plan = downloadPlan(report("given"));
    expect(plan).toMatchObject({
      request: { rotate_token: false }, incomplete: false });
    expect(plan.hint).toMatch(/generated AUTH_TOKEN/);
  });

  // The branches still differ, which is why the report is read rather than the
  // answer assumed: `reused` is a bundle that carries a real token, and saying
  // "placeholder" over it would send somebody looking for one it already has.
  it("keeps the branches apart even though they send the same request", () => {
    expect(downloadPlan(report("reused")).hint).toMatch(/already in that folder/);
    expect(downloadPlan(report("reused")).incomplete).toBe(false);
  });
});

// Still said, and still here -- but on step 1 now, beside the Regenerate that
// mints. The download step had a rotate box, and it is gone: minting belongs on
// the agent the credential is for, where what it kills is on screen.
describe("rotateHazard", () => {
  it("names the agent whose credential is about to stop working", () => {
    expect(rotateHazard("bbb222")).toContain("agent bbb222");
  });

  it("still says what happens when no agent is selected", () => {
    expect(rotateHazard(null)).toMatch(/0\/1 Running/);
    // No dangling "for agent undefined", which is what a template read as
    // optional-but-always-there produces.
    expect(rotateHazard(null)).not.toContain("for agent");
  });
});

// What this app still holds of a credential it minted (#123). These are about
// the states and never about the value: the pair that must not merge is
// "nothing was minted" against "nobody could be asked", which is the same
// distinction the Python side keeps between an empty read and a denied one.
describe("recalled", () => {
  it("reads a token as held and a null as none", () => {
    expect(recalled({ auth_token: "tok" })).toBe("held");
    expect(recalled({ auth_token: null })).toBe("none");
  });
});

describe("recallNote", () => {
  it("explains an empty field for the state that can explain it", () => {
    // The sentence that was the only one there was, for what used to be the
    // only case: an agent whose token was issued once, at creation.
    expect(recallNote("none")).toMatch(/cannot be read back/);
  });

  it("never says a token cannot be read back when it could not be asked for", () => {
    const unread = recallNote("unread");
    // The failure this exists to stop: an unreachable store rendering as a
    // statement about the agent -- "could not read" wearing "there is nothing
    // there", and wrong about exactly the agents this app made itself.
    expect(unread).not.toMatch(/cannot be read back/);
    expect(unread).toMatch(/could not ask/);
    expect(unread).not.toBe(recallNote("none"));
  });

  it("says nothing while the answer is outstanding, or once there is a token", () => {
    // Not the `none` sentence: until the answer lands the field is empty for a
    // reason that has nothing to do with the agent, and a claim made without
    // having asked is the same bug one tick earlier.
    expect(recallNote("asking")).toBeNull();
    expect(recallNote("held")).toBeNull();
  });
});
