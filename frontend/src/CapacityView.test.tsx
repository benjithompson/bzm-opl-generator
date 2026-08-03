// @vitest-environment jsdom
//
// The fold, through the controls rather than through the hook: foldSet.test.ts
// already pins what the set does, and what this file is for is that the header
// is wired to it, that the account's own figures do not move when a workspace
// is put away, and that "Collapse all" reaches the workspaces a filter is
// hiding -- which is the one part of it nobody can see going wrong.
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

const header = (name: string) =>
  screen.getByRole("button", { name: new RegExp(name) });

/** Is this workspace's detail folded away?
 *
 *  The fold is CSS -- the card is clipped and made `invisible` rather than
 *  unmounted -- so "not on screen" is not "not in the document", and a test
 *  asking queryByText would pass on a card that never folded. What it asks
 *  instead is what the header claims and what assistive technology is told,
 *  which is the same pair a browser acts on. */
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
  render(<CapacityView cap={cap} />);
  expect(within(detail("Alpha")).getByText("Dublin")).toBeTruthy();
  expect(folded("Alpha")).toBe(false);

  fireEvent.click(header("Alpha"));
  expect(folded("Alpha")).toBe(true);

  // The neighbour is untouched -- folding is per card, and the account total
  // is the account's whether or not anyone is looking at the parts.
  expect(folded("Bravo")).toBe(false);
  expect(screen.getByText("5,500")).toBeTruthy();

  fireEvent.click(header("Alpha"));
  expect(folded("Alpha")).toBe(false);
});

test("what stays on screen folded is the summary, not just the name", () => {
  render(<CapacityView cap={cap} />);
  fireEvent.click(header("Alpha"));

  // The point of folding to *this* line: 54 of these is an index of the
  // account, where 54 names would be a table of contents for nothing.
  const row = header("Alpha");
  expect(row.textContent).toMatch(/Alpha/);
  expect(row.textContent).toMatch(/1 location/);
  expect(row.textContent).toMatch(/5,000/);
  expect(row.textContent).toMatch(/91% of the account/);
});

test("Collapse all reaches the workspaces the filter is hiding", () => {
  render(<CapacityView cap={cap} />);
  const filter = screen.getByLabelText("Filter workspaces");

  // Narrow to one -- the filter removes the card entirely, which is not the
  // fold -- then fold everything.
  fireEvent.change(filter, { target: { value: "alpha" } });
  expect(screen.queryByText("Frankfurt")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  expect(folded("Alpha")).toBe(true);

  // Bravo was off screen when the button was pressed and is folded too:
  // otherwise clearing the filter brings back a card nobody asked to open,
  // and the button reads "Expand all" over a page that is half open.
  fireEvent.change(filter, { target: { value: "" } });
  expect(folded("Bravo")).toBe(true);
  expect(screen.getByRole("button", { name: "Expand all" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
  expect(folded("Alpha")).toBe(false);
  expect(folded("Bravo")).toBe(false);
});

test("the control offers the move that is left to make", () => {
  render(<CapacityView cap={cap} />);
  expect(screen.getByRole("button", { name: "Collapse all" })).toBeTruthy();

  // One of two folded is not all of them.
  fireEvent.click(header("Alpha"));
  expect(screen.getByRole("button", { name: "Collapse all" })).toBeTruthy();

  fireEvent.click(header("Bravo"));
  expect(screen.getByRole("button", { name: "Expand all" })).toBeTruthy();
});

test("the account bar is the account's, folded or not", () => {
  const { container } = render(<CapacityView cap={cap} />);
  const bar = () => container.querySelectorAll("[title$='rated VUs (91%)']");
  expect(bar().length).toBe(1);

  fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
  // Still one segment for Alpha, still 91%: the bar answers "where is this
  // account's capacity", which is not a question about what is unfolded.
  expect(bar().length).toBe(1);
  expect(within(screen.getByText("account rated VUs").parentElement!)
    .getByText("5,500")).toBeTruthy();
});
