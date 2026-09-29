// @vitest-environment jsdom
//
// The workspace fold through the controls: the header toggles it, the account
// figures do not move, and "Collapse all" reaches workspaces a filter hides.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { Capacity } from "./api";
import { CapacityView } from "./CapacityView";

afterEach(cleanup);

/** Two workspaces, each holding one location, with different totals so the
 *  order on screen is known. */
const cap: Capacity = {
  account_id: 1,
  workspaces: [{ id: 10, name: "Alpha" }, { id: 20, name: "Bravo" }],
  locations: [
    { id: "l-1", name: "Dublin", func_ids: ["performance"], agents: 2,
      agents_reporting: 2, agents_unknown: 0, slots: 5, threads_per_engine: 500,
      engines: 10, rated_vus: 5000, workspace_ids: [10],
      workspace_names: ["Alpha"], shared: false },
    { id: "l-2", name: "Frankfurt", func_ids: ["performance"], agents: 1,
      agents_reporting: 1, agents_unknown: 0, slots: 1, threads_per_engine: 500,
      engines: 1, rated_vus: 500, workspace_ids: [20],
      workspace_names: ["Bravo"], shared: false },
  ],
  rated_vus: 5500,
  unrated: 0,
};

/** The view; `refresh` defaults to a spy nothing asserts on. */
const view = (refresh: () => void = () => {}, refreshing = false) =>
  render(<CapacityView cap={cap} refresh={refresh} refreshing={refreshing} />);

const header = (name: string) =>
  screen.getByRole("button", { name: new RegExp(name) });

/** Is this workspace's detail folded? The fold is CSS, not unmounting, so this
 *  asks what the header and the accessibility tree say. */
function folded(name: string) {
  const h = header(name);
  const body = document.getElementById(h.getAttribute("aria-controls")!)!;
  const hidden = body.getAttribute("aria-hidden") === "true";
  expect(h.getAttribute("aria-expanded")).toBe(String(!hidden));
  return hidden;
}

/** The detail region, for asking what is inside it. */
const detail = (name: string) =>
  document.getElementById(header(name).getAttribute("aria-controls")!)!;

test("a workspace header folds its own card, and moves nothing else", () => {
  view();
  expect(within(detail("Alpha")).getByText("Dublin")).toBeTruthy();
  expect(folded("Alpha")).toBe(false);

  fireEvent.click(header("Alpha"));
  expect(folded("Alpha")).toBe(true);

  // The neighbour and the account total are untouched.
  expect(folded("Bravo")).toBe(false);
  expect(screen.getByText("5,500")).toBeTruthy();

  fireEvent.click(header("Alpha"));
  expect(folded("Alpha")).toBe(false);
});

test("what stays on screen folded is the summary, not just the name", () => {
  view();
  fireEvent.click(header("Alpha"));

  // Folded, the header line is the workspace's summary.
  const row = header("Alpha");
  expect(row.textContent).toMatch(/Alpha/);
  expect(row.textContent).toMatch(/1 location/);
  expect(row.textContent).toMatch(/5,000/);
  expect(row.textContent).toMatch(/91% of the account/);
  // Including the bar, which is outside the fold.
  expect(within(row).getByTitle(/^Dublin/)).toBeTruthy();
  expect(within(detail("Alpha")).queryByTitle(/^Dublin/)).toBeNull();
});

test("Collapse all reaches the workspaces the filter is hiding", () => {
  view();
  const filter = screen.getByLabelText("Filter workspaces");

  // Filter to one, then fold everything.
  fireEvent.change(filter, { target: { value: "alpha" } });
  expect(screen.queryByText("Frankfurt")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(folded("Alpha")).toBe(true);

  // The workspace hidden by the filter was folded too.
  fireEvent.change(filter, { target: { value: "" } });
  expect(folded("Bravo")).toBe(true);
  expect(screen.getByRole("button", { name: "Expand all" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
  expect(folded("Alpha")).toBe(false);
  expect(folded("Bravo")).toBe(false);
});

test("the control offers the move that is left to make", () => {
  view();
  expect(screen.getByRole("button", { name: "Collapse all" })).toBeTruthy();

  // One of two folded is not all of them.
  fireEvent.click(header("Alpha"));
  expect(screen.getByRole("button", { name: "Collapse all" })).toBeTruthy();

  fireEvent.click(header("Bravo"));
  expect(screen.getByRole("button", { name: "Expand all" })).toBeTruthy();
});

test("the account bar is the account's, folded or not", () => {
  const { container } = view();
  const bar = () => container.querySelectorAll("[title$='rated VUs (91%)']");
  expect(bar().length).toBe(1);

  fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  // The account bar is unchanged by folding.
  expect(bar().length).toBe(1);
  expect(within(screen.getByText("account rated VUs").parentElement!)
    .getByText("5,500")).toBeTruthy();
});

test("Refresh asks for the account again, and says so while it does", () => {
  const asked: number[] = [];
  view(() => asked.push(1));

  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(asked.length).toBe(1);

  // In flight it stops taking clicks.
  cleanup();
  view(() => asked.push(1), true);
  const button = screen.getByRole<HTMLButtonElement>(
    "button", { name: "Refresh" });
  expect(button.disabled).toBe(true);
  fireEvent.click(button);
  expect(asked.length).toBe(1);
});
