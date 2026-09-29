// @vitest-environment jsdom
//
// The images view through its controls: the source in the heading, the notices
// for what could not be read, the filter, the copies and the downloads.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ImagesAnswer } from "./api";
import {
  catalogueImages, imageRow, locationImages, pinnedCatalogueImages,
} from "./fakeApi";
import { CatalogueReason, ImagesView } from "./ImagesView";

afterEach(cleanup);
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

// Only a covered funcId is named with no account connected.
const labelOf = (id: string) =>
  ({ performance: "Performance" } as Record<string, string>)[id] ?? null;

/** The view. A catalogue answer is asked for with no location, so its reason
 *  defaults to "disconnected"; a location's has none. */
function view(answer: ImagesAnswer | null, over: {
  all?: boolean; setAll?: (v: boolean) => void; error?: string | null;
  busy?: boolean; registry?: string | null; reason?: CatalogueReason | null;
} = {}) {
  const reason = over.reason !== undefined ? over.reason
    : answer?.source === "catalogue" ? "disconnected" : null;
  return render(
    <ImagesView answer={answer} busy={over.busy ?? false} error={over.error ?? null}
      all={over.all ?? false} setAll={over.setAll ?? (() => {})}
      labelOf={labelOf} registry={over.registry ?? null}
      catalogueReason={reason} />);
}

/** Clipboard writes, recorded. */
function stubClipboard() {
  const written: string[] = [];
  vi.stubGlobal("navigator", {
    clipboard: { writeText: async (t: string) => { written.push(t); } },
  });
  return written;
}

/** Blobs handed to the browser, by file name. */
function stubDownloads() {
  const saved: { name: string; blob: Blob }[] = [];
  let last: Blob | null = null;
  const u = URL as unknown as Record<string, unknown>;
  u.createObjectURL = (b: Blob) => { last = b; return "blob:x"; };
  u.revokeObjectURL = () => {};
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement) { saved.push({ name: this.download, blob: last! }); });
  return saved;
}

test("catalogue mode says so, and says how to get a location's versions", () => {
  view(catalogueImages());
  expect(screen.getByRole("heading", { name: "BlazeMeter's image catalogue" }))
    .toBeTruthy();
  expect(screen.getByText(/Connect an account and choose a location/)).toBeTruthy();
  // Nobody can say what a location requires, so there is no column for it.
  expect(screen.queryByRole("columnheader", { name: "required" })).toBeNull();
  // The mutable tag is a warning, not "pinned", and says what it is now.
  expect(screen.getByText(/latest names a different image/)).toBeTruthy();
  expect(screen.getByText("latest = 1.16.30")).toBeTruthy();
  // Nothing in the catalogue is required of a location, so there is no
  // Required-only filter to offer, and the page says why.
  expect(screen.queryByRole("radiogroup")).toBeNull();
  expect(screen.getByText(/catalogue lists every image it knows/)).toBeTruthy();
});

test("a mutable tag nobody resolved says unread or no match, never one for the other", () => {
  const latest = { tag: "latest", ref: "v4:latest", tag_mutable: true,
                   resolves_to: null };
  view(locationImages({ images: [
    imageRow({ ...latest }),
    imageRow({ ...latest, key: "b", ref: "b:latest", registry_state: "unread",
               registry_detail: "HTTP 429", digest: null, size_mb: null }),
  ] }));
  const rows = within(screen.getByRole("table")).getAllByRole("row");
  expect(within(rows[1]).getByText("no version tag has the digest of latest"))
    .toBeTruthy();
  const unread = within(rows[2]).getByText("the version behind latest was not read");
  expect(unread.getAttribute("title")).toBe("HTTP 429");
  expect(rows[2].textContent).not.toMatch(/no version tag/);
});

test("a funcId nothing served names is shown as the funcId, in code type", () => {
  view(locationImages({ images: [
    imageRow({ functionalities: ["performance", "functionalApi"] })] }));
  const named = screen.getByText("Performance");
  const raw = screen.getByText("functionalApi");
  expect(named.className).not.toMatch(/font-mono/);
  expect(raw.className).toMatch(/font-mono/);
  expect(raw.getAttribute("title")).toMatch(/connect an account/);
});

test("a catalogue pinned to the newest releases says those are not a location's", () => {
  view(pinnedCatalogueImages());
  expect(screen.getByText(/pinned to the newest release/)).toBeTruthy();
  expect(screen.getByRole("note").textContent)
    .toMatch(/older release than the newest.*Connect an account/);
  expect(screen.getByText(/tag from the newest release in BlazeMeter's registry, not your location/))
    .toBeTruthy();
  // Pinned, so no mutable-tag warning.
  expect(screen.queryByText(/names a different image/)).toBeNull();
});

test("the catalogue hint follows why there is no location", () => {
  view(catalogueImages(), { reason: "no-location" });
  expect(screen.getByText(/Choose a location under Generate/)).toBeTruthy();
});

test("location mode names the location and marks what it requires", () => {
  view(locationImages({
    images: [imageRow(),
             imageRow({ ref: "blazemeter/extra:1", key: "extra", required: false })],
  }), { all: true });
  expect(screen.getByRole("heading",
    { name: "Images for location Dublin (read from your account)" })).toBeTruthy();
  const table = screen.getByRole("table", { name: "Engines images" });
  const rows = within(table).getAllByRole("row");
  expect(within(rows[1]).getByText("required")).toBeTruthy();
  expect(within(rows[2]).getByText("not for this location")).toBeTruthy();
  expect(within(rows[1]).getByText("Performance")).toBeTruthy();
  expect(within(rows[1]).getByText("812 MB")).toBeTruthy();
  expect(within(rows[1]).getByText("pinned")).toBeTruthy();
});

test("rows are grouped under their category", () => {
  view(locationImages({
    images: [imageRow(), imageRow({ ref: "crane:1", key: "crane", category: "Agent" })],
  }));
  expect(within(screen.getByRole("region", { name: "Agent" })).getByText("crane:1"))
    .toBeTruthy();
  expect(within(screen.getByRole("region", { name: "Engines" }))
    .queryByText("crane:1")).toBeNull();
});

test("an unread version list and an unread registry each say so", () => {
  view(locationImages({
    image_list_state: "unread",
    registry_lookup: { state: "unread", detail: "docker.io timed out." },
    images: [imageRow({ registry_state: "unread", registry_detail: "timed out",
                        digest: null, size_mb: null, newest_tag: null,
                        update_available: null })],
  }));
  const notes = screen.getAllByRole("note").map((n) => n.textContent);
  expect(notes.some((t) => /version list could not be read/.test(t ?? ""))).toBe(true);
  expect(notes.some((t) => /registry could not be read.*docker\.io timed out/
    .test(t ?? ""))).toBe(true);
  // Size and digest say "not read", never a blank or a zero.
  const row = within(screen.getByRole("table")).getAllByRole("row")[1];
  expect(within(row).getAllByText("not read")).toHaveLength(2);
  expect(row.textContent).not.toMatch(/\b0 MB\b/);
});

test("an agent-less location and a partial lookup each say so", () => {
  view(locationImages({
    image_list_state: "no-agent",
    registry_lookup: { state: "partial", detail: null },
  }));
  const notes = screen.getAllByRole("note").map((n) => n.textContent).join(" ");
  expect(notes).toMatch(/no agent yet/);
  expect(notes).toMatch(/some images only/);
});

test("a newer tag is named on its row", () => {
  view(locationImages({
    images: [imageRow({ update_available: true, newest_tag: "1.17.0" })],
  }));
  expect(screen.getByText("newer tag available: 1.17.0")).toBeTruthy();
  expect(screen.queryByText("pinned")).toBeNull();
});

test("an inferred purpose is marked", () => {
  view(locationImages({ images: [imageRow({ verified: false })] }));
  expect(screen.getByTitle(/inferred from the image/)).toBeTruthy();
});

test("the filter asks for all, and back", () => {
  const setAll = vi.fn();
  view(locationImages(), { setAll });
  const required = screen.getByRole("radio", { name: /Required only/ });
  expect(required.getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("radio", { name: /^All/ }));
  expect(setAll).toHaveBeenLastCalledWith(true);
});

test("an empty required list says how to see the rest", () => {
  view(locationImages({ images: [] }));
  expect(screen.getByText(/No images are listed as required/)).toBeTruthy();
});

test("a failed read is an error, with no table and no empty-list sentence", () => {
  view(null, { error: "upstream 502" });
  expect(screen.getByText("upstream 502")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByText(/No images are listed/)).toBeNull();
});

test("Copy puts every reference on the clipboard; a row copies its own", async () => {
  const written = stubClipboard();
  view(locationImages({ images: [imageRow(), imageRow({ ref: "b:2", key: "b" })] }));
  fireEvent.click(screen.getByRole("button", { name: "Copy" }));
  await waitFor(() => expect(written).toEqual(["blazemeter/v4:1.16.30\nb:2\n"]));

  fireEvent.click(screen.getByRole("button", { name: "Copy b:2" }));
  await waitFor(() => expect(written[1]).toBe("b:2"));

  fireEvent.click(screen.getByRole("button",
    { name: "Copy the digest of blazemeter/v4:1.16.30" }));
  await waitFor(() => expect(written[2]).toMatch(/^sha256:0123/));
});

test("CSV and Markdown download the rows shown", async () => {
  const saved = stubDownloads();
  view(locationImages(), { registry: "registry.corp" });
  fireEvent.click(screen.getByRole("button", { name: "CSV" }));
  fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
  expect(saved.map((s) => s.name))
    .toEqual(["bzm-opl-images-dublin.csv", "bzm-opl-images-dublin.md"]);
  expect(await saved[0].blob.text()).toMatch(/^ref,repo,tag/);
  expect(await saved[1].blob.text())
    .toMatch(/bzm-opl-gen images --verify registry\.corp/);
});

test("the drift check uses the bundle's registry when set", () => {
  view(locationImages(), { registry: "registry.corp:5000" });
  expect(screen.getByText("bzm-opl-gen images --verify registry.corp:5000"))
    .toBeTruthy();
});
