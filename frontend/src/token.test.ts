import { describe, expect, it } from "vitest";
import { TokenReport } from "./api";
import { downloadPlan, recallNote, recalled, rotateHazard } from "./token";

const report = (branch: TokenReport["branch"]): TokenReport =>
  ({ branch, ship_id: "bbb222", message: "…" });

describe("downloadPlan", () => {
  it("never rotates", () => {
    for (const b of ["given", "rotated", "placeholder"] as const) {
      // The request as sent: nothing left for a caller to convert.
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

  // Before the first preview, the hint takes the cautious sentence while
  // `incomplete` is "unread", so no AUTH_TOKEN row appears.
  it("says the hint's cautious sentence before anything has been generated",
    () => {
      expect(downloadPlan(null).hint).toMatch(/left as a placeholder/);
    });

  it("does not claim the token is missing before anything has been generated",
    () => {
      expect(downloadPlan(null).incomplete).toBe("unread");
    });

  it("treats a token in the form as complete", () => {
    const plan = downloadPlan(report("given"));
    expect(plan).toMatchObject({
      request: { rotate_token: false }, incomplete: false });
    expect(plan.hint).toMatch(/generated AUTH_TOKEN/);
  });

  // The branches differ in what they say though every request is the same.
  it("keeps the branches apart even though they send the same request", () => {
    expect(downloadPlan(report("reused")).hint).toMatch(/already in that folder/);
    expect(downloadPlan(report("reused")).incomplete).toBe(false);
  });
});

describe("rotateHazard", () => {
  it("names the agent whose credential is about to stop working", () => {
    expect(rotateHazard("bbb222")).toContain("agent bbb222");
  });

  it("still says what happens when no agent is selected", () => {
    expect(rotateHazard(null)).toMatch(/0\/1 Running/);
    // No "for agent undefined".
    expect(rotateHazard(null)).not.toContain("for agent");
  });
});

// What the server holds of a minted token: "holds none" and "could not ask"
// must stay apart.
describe("recalled", () => {
  it("reads a token as held and a null as none", () => {
    expect(recalled({ auth_token: "tok" })).toBe("held");
    expect(recalled({ auth_token: null })).toBe("none");
  });
});

describe("recallNote", () => {
  it("explains an empty field for the state that can explain it", () => {
    // Only "none" says the token cannot be read back.
    expect(recallNote("none")).toMatch(/cannot be read back/);
  });

  it("never says a token cannot be read back when it could not be asked for", () => {
    const unread = recallNote("unread");
    // An unreachable store makes no claim about the agent.
    expect(unread).not.toMatch(/cannot be read back/);
    expect(unread).toMatch(/could not ask/);
    expect(unread).not.toBe(recallNote("none"));
  });

  it("says nothing while the answer is outstanding, or once there is a token", () => {
    // Nothing is said until the answer lands.
    expect(recallNote("asking")).toBeNull();
    expect(recallNote("held")).toBeNull();
  });
});
