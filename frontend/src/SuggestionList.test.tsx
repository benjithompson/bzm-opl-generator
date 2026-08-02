// @vitest-environment jsdom
//
// What a suggestion row puts on screen, as opposed to what it offers.
// suggestions.test.ts covers the offer -- data in, data out, no DOM -- and this
// covers the other half: every value on the row arrives already written, and
// the one refusal a row can carry arrives as a sentence.
//
// The fixtures below are deliberately impossible to recompose in the browser:
// the served string for the proxy object carries the space after the colon that
// `json.dumps` writes and `JSON.stringify` does not, and the blocked sentence is
// served against options that hold no CA mode at all. Either one being derived
// here rather than rendered would fail these.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { MergeState, Options, Strength, Suggestion } from "./api";
import { SuggestionList } from "./SuggestionList";
import { NOTHING_APPLIED, record } from "./suggestions";

afterEach(cleanup);

const sugg = (over: Partial<Suggestion> = {}): Suggestion => ({
  option: "platform", strength: "DECISIVE" as Strength, value: "k8s",
  candidates: ["k8s"], ruled_out: [], evidence: ["api_groups.openshift_security"],
  detail: "security.openshift.io is not served, so this is plain Kubernetes",
  state: "FILL" as MergeState, current: "openshift",
  current_shown: "openshift", value_shown: "k8s", candidates_shown: ["k8s"],
  ruled_out_shown: [], blocked: null,
  ...over,
});

function list(suggestions: Suggestion[], options: Options = {},
              applied = NOTHING_APPLIED) {
  return render(
    <SuggestionList suggestions={suggestions} whyNothing={null}
      options={options} applied={applied}
      onApply={() => {}} onUndo={() => {}} />);
}

// The proxy suggestion: an object, written the way profile.json would carry it.
const PROXY = { https: "http://proxy.corp:3128" };
const PROXY_SHOWN = '{"https": "http://proxy.corp:3128"}';

test("shows each value the way the file that would carry it writes it", () => {
  list([sugg({ option: "proxy", value: PROXY, candidates: [PROXY],
               current: null, current_shown: "not set",
               value_shown: PROXY_SHOWN, candidates_shown: [PROXY_SHOWN] })]);
  expect(screen.getByText(PROXY_SHOWN)).toBeTruthy();
  // ...including the option that holds nothing, which is said in words rather
  // than printed as null.
  expect(screen.getByText("not set")).toBeTruthy();
});

test("shows what the evidence ruled out in the same terms", () => {
  list([sugg({ option: "sv_ingress", strength: "SUGGESTIVE", value: null,
               candidates: ["contour"], ruled_out: [false, "istio"],
               state: "CHOOSE", current: null, current_shown: "not set",
               value_shown: "not set", candidates_shown: ["contour"],
               ruled_out_shown: ["false", "istio"] })]);
  expect(screen.getByText(/rules out false, istio/)).toBeTruthy();
});

test("says why a row is not offered, in the words it was served", () => {
  // Nothing in these options holds a CA mode: the refusal is generate's, made
  // where the one-of rule is, and this page states it rather than deciding it.
  const blocked = "custom CA trust already uses an inline PEM — clear it "
    + "first, because a bundle carrying two CA modes does not generate";
  list([sugg({ option: "ca_openshift_inject", strength: "SUGGESTIVE",
               value: null, candidates: [true], state: "CHOOSE",
               current: false, current_shown: "false", value_shown: "not set",
               candidates_shown: ["true"], blocked })], {});
  expect(screen.getByText(`Not offered: ${blocked}`)).toBeTruthy();
  // ...and no button, because the explanation is the answer.
  expect(screen.queryByRole("button", { name: /Use/ })).toBeNull();
});

test("labels the buttons with the served value, clipped", () => {
  list([sugg({ option: "proxy", value: PROXY, candidates: [PROXY],
               current: null, current_shown: "not set",
               value_shown: PROXY_SHOWN, candidates_shown: [PROXY_SHOWN] })]);
  const apply = screen.getByRole("button", { name: /Apply/ });
  // Cut to what a button can hold -- the row above shows it in full.
  expect(apply.textContent).toContain("…");
  expect(apply.textContent!.length).toBeLessThan(PROXY_SHOWN.length);
});

test("offers the undo in the words the row it was applied from used", () => {
  // The value undo restores is one only the browser holds -- it was on a row
  // that has since been re-rendered -- so what it was shown as is remembered
  // with it rather than formatted again here.
  const applied = record(NOTHING_APPLIED, "sv_ingress",
                         { value: null, shown: "not set" }, "contour");
  list([sugg({ option: "sv_ingress", value: "contour", candidates: ["contour"],
               state: "SETTLED", current: "contour",
               current_shown: "contour", value_shown: "contour",
               candidates_shown: ["contour"] })],
       { sv_ingress: "contour" }, applied);
  expect(screen.getByRole("button", { name: /Undo → not set/ })).toBeTruthy();
});
