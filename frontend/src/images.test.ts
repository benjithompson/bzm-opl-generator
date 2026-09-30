// The words and exports the images view builds from served rows: an unread
// registry field is never blank or zero, and CSV/Markdown carry what is shown.
import { expect, test } from "vitest";

import {
  catalogueImages, imageRow, locationImages, pinnedCatalogueImages,
} from "./fakeApi";
import {
  catalogueLine, digestText, newestNotice, driftNote, fileStem, functionalityNames, groupByCategory,
  listNotice, refsText, registryNotice, resolvedText, sizeText, sourceHeading,
  tagNotes, toCsv, toMarkdown,
} from "./images";

// Only a covered funcId is named with no account connected.
const labelOf = (id: string) =>
  ({ performance: "Performance" } as Record<string, string>)[id] ?? null;

test("a catalogue pinned to the newest releases says so, and that a location can differ", () => {
  const pinned = pinnedCatalogueImages();
  expect(catalogueLine(pinned)).toMatch(/pinned to the newest release/);
  expect(catalogueLine(catalogueImages())).toBe("Built-in list. Tags can be latest.");
  const note = newestNotice(pinned);
  expect(note).toMatch(/not read from a location/);
  expect(note).toMatch(/older release than the newest/);
  expect(note).toMatch(/Connect an account/);
  expect(note).not.toMatch(/`|--|\*|->/);
  expect(newestNotice(catalogueImages())).toBeNull();
  expect(newestNotice(locationImages())).toBeNull();

  const lines = toCsv(pinned.images, labelOf).split("\r\n");
  const head = lines[0].split(",");
  expect(lines[1].split(",")[head.indexOf("tag_source")]).toBe("registry-newest");

  const md = toMarkdown(pinned, labelOf, null);
  expect(md).toContain("> These versions were not read from a location.");
  expect(md).toContain("(`registry-newest`)");
});

test("a funcId nothing served names stays a funcId, in the caller's format", () => {
  const r = imageRow({ functionalities: ["performance", "functionalApi"] });
  expect(functionalityNames(r, labelOf)).toEqual(["Performance", "functionalApi"]);
  expect(functionalityNames(r, labelOf, (id) => `\`${id}\``))
    .toEqual(["Performance", "`functionalApi`"]);
});

test("a mutable tag says what it resolves to, and unread is never no match", () => {
  const latest = { tag: "latest", tag_mutable: true };
  expect(resolvedText(imageRow({ ...latest, resolves_to: "2.4.538-reduced" })))
    .toBe("latest = 2.4.538-reduced");
  expect(resolvedText(imageRow({ ...latest, resolves_to: null })))
    .toBe("no version tag has the digest of latest");
  expect(resolvedText(imageRow({ ...latest, resolves_to: null,
                                 registry_state: "unread" })))
    .toBe("the version behind latest was not read");
  expect(resolvedText(imageRow({ ...latest, resolves_to: null,
                                 registry_state: "not-asked" })))
    .toMatch(/not asked/);
  // A server that does not resolve tags says nothing, so neither does the page.
  const older = imageRow({ ...latest });
  delete older.resolves_to;
  expect(resolvedText(older)).toBeNull();
  // A pinned tag has nothing to resolve.
  expect(resolvedText(imageRow())).toBeNull();
});

test("rows group by category in the order the server sent them", () => {
  const rows = [imageRow({ ref: "a", category: "Agent" }),
                imageRow({ ref: "b", category: "Engines" }),
                imageRow({ ref: "c", category: "Agent" })];
  expect(groupByCategory(rows).map((g) => [g.category, g.rows.map((r) => r.ref)]))
    .toEqual([["Agent", ["a", "c"]], ["Engines", ["b"]]]);
});

test("a registry field says unread, not asked, or not reported, never empty or zero", () => {
  const read = imageRow();
  expect(sizeText(read)).toBe("812 MB");
  expect(digestText(read)).toBe("0123456789ab");

  const unread = imageRow({ registry_state: "unread", size_mb: null, digest: null,
                            registry_detail: "HTTP 429" });
  expect(sizeText(unread)).toBe("not read");
  expect(digestText(unread)).toBe("not read");

  expect(sizeText(imageRow({ registry_state: "not-asked", size_mb: null })))
    .toBe("not asked");
  // Read, and the registry had nothing to say: a different answer from unread.
  expect(sizeText(imageRow({ size_mb: null }))).toBe("not reported");
  // A zero that was read is a zero.
  expect(sizeText(imageRow({ size_mb: 0 }))).toBe("0 MB");
});

test("the tag says pinned, mutable, or which newer tag exists", () => {
  expect(tagNotes(imageRow())).toEqual([{ kind: "pinned", text: "pinned" }]);
  const newer = tagNotes(imageRow({ update_available: true, newest_tag: "1.17.2" }));
  expect(newer).toEqual([{ kind: "newer", text: "newer tag available: 1.17.2" }]);
  const both = tagNotes(imageRow({ tag: "latest", tag_mutable: true,
                                   update_available: true, newest_tag: "2" }));
  expect(both.map((t) => t.kind)).toEqual(["newer", "mutable"]);
  expect(both[1].text).toMatch(/latest names a different image/);
  // Unknown is not "no update".
  expect(tagNotes(imageRow({ update_available: null, newest_tag: null })))
    .toEqual([{ kind: "pinned", text: "pinned" }]);
});

test("Copy is every reference, one per line", () => {
  expect(refsText([imageRow({ ref: "a:1" }), imageRow({ ref: "b:2" })]))
    .toBe("a:1\nb:2\n");
  expect(refsText([])).toBe("");
});

test("the heading names the location, or says it is the catalogue", () => {
  expect(sourceHeading(locationImages()))
    .toBe("Images for location Dublin (read from your account)");
  expect(sourceHeading(catalogueImages())).toBe("BlazeMeter's image catalogue");
});

test("an unread version list and an unread registry each get a sentence", () => {
  expect(listNotice(locationImages())).toBeNull();
  expect(listNotice(catalogueImages())).toBeNull();
  expect(listNotice(locationImages({ image_list_state: "unread" })))
    .toMatch(/could not be read/);
  expect(listNotice(locationImages({ image_list_state: "no-agent" })))
    .toMatch(/no agent yet/);

  expect(registryNotice(locationImages())).toBeNull();
  expect(registryNotice(locationImages({
    registry_lookup: { state: "unread", detail: "docker.io timed out." } })))
    .toMatch(/could not be read.*docker\.io timed out\./);
  expect(registryNotice(locationImages({
    registry_lookup: { state: "partial", detail: null } })))
    .toMatch(/some images only/);
  expect(registryNotice(catalogueImages({
    registry_lookup: { state: "not-asked", detail: null } }))).toBeNull();
});

test("the notices are plain prose", () => {
  const texts = [
    listNotice(locationImages({ image_list_state: "unread" })),
    listNotice(locationImages({ image_list_state: "no-agent" })),
    registryNotice(locationImages({ registry_lookup: { state: "partial", detail: null } })),
    driftNote(null).text,
  ];
  for (const t of texts) expect(t).not.toMatch(/`|--|\*|->/);
});

test("the drift check names the bundle's registry, or a lower-case sample", () => {
  expect(driftNote("registry.corp:5000").command)
    .toBe("bzm-opl-gen images --verify registry.corp:5000");
  expect(driftNote(null).command).toBe("bzm-opl-gen images --verify <registry>");
});

test("CSV quotes what needs it and keeps the registry state beside blank fields", () => {
  const rows = [
    imageRow({ purpose: 'Runs "the" test, and more',
               functionalities: ["performance", "functionalApi"] }),
    imageRow({ ref: "x:1", registry_state: "unread", digest: null, size_mb: null,
               newest_tag: null, update_available: null, required: false }),
    imageRow({ ref: "y:latest", tag: "latest", tag_mutable: true,
               resolves_to: "2.4.538-reduced" }),
  ];
  const lines = toCsv(rows, labelOf).split("\r\n");
  const head = lines[0].split(",");
  expect(head).toContain("registry_state");
  expect(lines[1]).toContain('"Runs ""the"" test, and more"');
  const first = lines[1].split(",");
  expect(first[head.indexOf("func_ids")]).toBe("performance; functionalApi");
  expect(first[head.indexOf("functionalities")]).toBe("Performance; functionalApi");
  const unread = lines[2].split(",");
  expect(unread[head.indexOf("registry_state")]).toBe("unread");
  expect(unread[head.indexOf("size_mb")]).toBe("");
  expect(unread[head.indexOf("required")]).toBe("no");
  expect(lines[3].split(",")[head.indexOf("resolves_to")]).toBe("2.4.538-reduced");
  expect(lines[4]).toBe("");
});

test("CSV leaves required blank in catalogue mode, where nobody can say", () => {
  const lines = toCsv(catalogueImages().images, labelOf).split("\r\n");
  const head = lines[0].split(",");
  expect(lines[1].split(",")[head.indexOf("required")]).toBe("");
});

test("Markdown has the heading, the notices, a table per category and the drift check", () => {
  const md = toMarkdown(locationImages({
    image_list_state: "unread",
    images: [imageRow({ purpose: "a | b", verified: false,
                        functionalities: ["performance", "proxyRecorder"] }),
             imageRow({ ref: "c:1", category: "Agent", update_available: true,
                        newest_tag: "2" }),
             imageRow({ ref: "d:latest", category: "Agent", tag: "latest",
                        tag_mutable: true, resolves_to: "2.4.538-reduced" })],
  }), labelOf, "registry.corp");
  expect(md).toMatch(/^# Images for location Dublin/);
  expect(md).toMatch(/> The location's version list could not be read/);
  expect(md).toMatch(/## Engines\n\n\| Image .* Resolves to \| Tag from \| Required \|/);
  expect(md).toContain("Performance, `proxyRecorder`");
  expect(md).toContain("| `2.4.538-reduced` |");
  expect(md).toMatch(/## Agent/);
  expect(md).toContain("a \\| b (inferred)");
  expect(md).toContain("newer tag available: 2");
  expect(md).toContain("`bzm-opl-gen images --verify registry.corp`");
});

test("Markdown in catalogue mode has no required column", () => {
  const md = toMarkdown(catalogueImages(), labelOf, null);
  expect(md).toMatch(/^# BlazeMeter's image catalogue/);
  expect(md).not.toMatch(/Required/);
});

test("the file name comes from the location, or says catalogue", () => {
  expect(fileStem(locationImages())).toBe("bzm-opl-images-dublin");
  expect(fileStem(locationImages({
    location: { harbor_id: "h", name: "EU / West 1", func_ids: [] } })))
    .toBe("bzm-opl-images-eu-west-1");
  expect(fileStem(catalogueImages())).toBe("bzm-opl-images-catalogue");
});
