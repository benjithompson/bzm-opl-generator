import { describe, expect, it } from "vitest";
import { TokenReport } from "./api";
import { downloadPlan, rotateHazard } from "./token";

const report = (branch: TokenReport["branch"]): TokenReport =>
  ({ branch, ship_id: "bbb222", message: "…" });

describe("downloadPlan", () => {
  it("does not rotate by default, which is the whole of #64", () => {
    for (const b of ["given", "reused", "placeholder"] as const) {
      expect(downloadPlan(report(b), false, "bbb222").rotates).toBe(false);
    }
  });

  it("says a bundle with no token cannot be applied yet", () => {
    const plan = downloadPlan(report("placeholder"), false, "bbb222");
    expect(plan.incomplete).toBe(true);
    expect(plan.hint).toMatch(/fill it in/);
  });

  // Before the first preview lands there is nothing to go on, and the honest
  // answer is the one that understates the bundle rather than the one that
  // claims a token is in it.
  it("assumes the placeholder before anything has been generated", () => {
    expect(downloadPlan(null, false, null).incomplete).toBe(true);
  });

  it("treats a token in the form as complete, and rotates nothing", () => {
    const plan = downloadPlan(report("given"), false, "bbb222");
    expect(plan).toMatchObject({ rotates: false, incomplete: false, warning: null });
    expect(plan.hint).toMatch(/as entered/);
  });

  it("reports a folder's own token as what a save will carry", () => {
    expect(downloadPlan(report("reused"), false, "bbb222").hint)
      .toMatch(/already in that folder/);
  });

  it("warns what a rotation breaks before it is asked for, not after", () => {
    const plan = downloadPlan(report("placeholder"), true, "bbb222");
    expect(plan.rotates).toBe(true);
    expect(plan.warning).toContain("bbb222");
    expect(plan.warning).toMatch(/0\/1/);
    // A rotation is not the bundle being incomplete -- it comes out applicable.
    expect(plan.incomplete).toBe(false);
  });

  // core answers this contradiction rather than obeying it: minting and then
  // writing the supplied token over it would revoke the one that was pasted and
  // put nothing usable in the bundle. The page must describe the bundle it is
  // actually going to hand over.
  it("lets a token in the form win over the rotate choice, and says so", () => {
    const plan = downloadPlan(report("given"), true, "bbb222");
    expect(plan.rotates).toBe(false);
    expect(plan.hint).toMatch(/nothing will be issued/);
  });
});

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
