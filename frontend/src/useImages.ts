import { Api, ImagesAnswer } from "./api";
import { goneNotice } from "./stale";
import { useResource } from "./useResource";

/** What the images view asks: a location's versions, or the catalogue (null). */
interface ImagesAsk { harborId: string | null; all: boolean }

const same = (a: ImagesAsk, b: ImagesAsk) =>
  a.harborId === b.harborId && a.all === b.all;

/** The image list for the images view: read on arriving at the view, and again
 *  whenever the location or the filter changes.
 *
 *  `harborId` is null for the catalogue. An answer is shown only for the ask
 *  now on screen, so another location's list never stands in while the new one
 *  is read; a failed read is an error, never an empty list. */
export function useImages(api: Api, active: boolean, harborId: string | null,
                          all: boolean) {
  const ask: ImagesAsk = { harborId, all };
  const read = useResource<{ ask: ImagesAsk; answer: ImagesAnswer }>(
    () => (active
      ? api.images(harborId, all).then(
        (answer) => ({ ask: { harborId, all }, answer }),
        (e: unknown) => {
          // A 404 on a location is the location gone, which Refresh fixes.
          throw new Error(goneNotice(e, "location")
            ?? (e instanceof Error ? e.message : String(e)));
        })
      : null),
    [api, active, harborId, all]);
  const current = read.data && same(read.data.ask, ask) ? read.data.answer : null;
  return {
    answer: current,
    busy: read.state === "loading",
    error: read.state === "error" ? read.error : null,
  };
}
