// @vitest-environment jsdom
//
// The images read: only while the view is open, keyed on the location and the
// filter, with another ask's answer never standing in for the current one.
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { ApiError, ImagesAnswer } from "./api";
import {
  catalogueImages, deferred, fakeApi, locationImages,
} from "./fakeApi";
import { useImages } from "./useImages";

afterEach(cleanup);

type Ask = [string | null, boolean];

/** A fake whose /api/images records each ask and answers from `answer`. */
function recording(answer: (harborId: string | null, all: boolean) => Promise<ImagesAnswer>) {
  const asked: Ask[] = [];
  const api = fakeApi({
    images: (harborId, all) => { asked.push([harborId, all]); return answer(harborId, all); },
  });
  return { api, asked };
}

test("nothing is read while the view is closed", async () => {
  const { api, asked } = recording(async () => catalogueImages());
  const { result } = renderHook(() => useImages(api, false, "h-perf", false));
  await act(async () => {});
  expect(asked).toEqual([]);
  expect(result.current).toEqual({ answer: null, busy: false, error: null });
});

test("no location asks for the catalogue", async () => {
  const { api, asked } = recording(async () => catalogueImages());
  const { result } = renderHook(() => useImages(api, true, null, false));
  await act(async () => {});
  expect(asked).toEqual([[null, false]]);
  expect(result.current.answer?.source).toBe("catalogue");
});

test("a location change re-reads, and the old answer is not shown meanwhile", async () => {
  const second = deferred<ImagesAnswer>();
  const { api, asked } = recording((h) => (h === "h-1"
    ? Promise.resolve(locationImages())
    : second.promise));
  const { result, rerender } = renderHook(
    ({ h }) => useImages(api, true, h, false), { initialProps: { h: "h-1" } });
  await act(async () => {});
  expect(result.current.answer?.location?.name).toBe("Dublin");

  rerender({ h: "h-2" });
  expect(asked).toEqual([["h-1", false], ["h-2", false]]);
  // Dublin's list must not stand in for the location now selected.
  expect(result.current.answer).toBeNull();
  expect(result.current.busy).toBe(true);

  await act(async () => {
    second.settle(locationImages({
      location: { harbor_id: "h-2", name: "Frankfurt", func_ids: [] } }));
    await second.promise;
  });
  expect(result.current.answer?.location?.name).toBe("Frankfurt");
  expect(result.current.busy).toBe(false);
});

test("the filter is part of the ask", async () => {
  const { api, asked } = recording(async () => locationImages());
  const { rerender } = renderHook(
    ({ all }) => useImages(api, true, "h-1", all), { initialProps: { all: false } });
  await act(async () => {});
  rerender({ all: true });
  await act(async () => {});
  expect(asked).toEqual([["h-1", false], ["h-1", true]]);
});

test("a slow answer for the previous location is dropped", async () => {
  const slow = deferred<ImagesAnswer>();
  const { api } = recording((h) => (h === "h-1"
    ? slow.promise : Promise.resolve(locationImages({
      location: { harbor_id: "h-2", name: "Frankfurt", func_ids: [] } }))));
  const { result, rerender } = renderHook(
    ({ h }) => useImages(api, true, h, false), { initialProps: { h: "h-1" } });
  rerender({ h: "h-2" });
  await act(async () => {});
  await act(async () => { slow.settle(locationImages()); await slow.promise; });
  expect(result.current.answer?.location?.name).toBe("Frankfurt");
});

test("a refused read is an error, not an empty list", async () => {
  const { api } = recording(async () => { throw new ApiError("upstream 502", 502); });
  const { result } = renderHook(() => useImages(api, true, "h-1", false));
  await act(async () => {});
  expect(result.current).toEqual({ answer: null, busy: false, error: "upstream 502" });
});

test("a 404 is the location gone, and says to press Refresh", async () => {
  const { api } = recording(async () => { throw new ApiError("no such harbor", 404); });
  const { result } = renderHook(() => useImages(api, true, "h-1", false));
  await act(async () => {});
  expect(result.current.error).toMatch(/This location no longer exists.*Refresh/);
});
