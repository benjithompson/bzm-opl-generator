// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { deferred } from "./fakeApi";
import { useResource } from "./useResource";

afterEach(cleanup);

test("a fetcher that returns null leaves the resource unread", () => {
  const { result } = renderHook(() => useResource(() => null, []));
  expect(result.current).toEqual({ state: "unread", data: null, error: null });
});

test("an answer lands as ok", async () => {
  const d = deferred<string>();
  const { result } = renderHook(() => useResource(() => d.promise, []));
  expect(result.current.state).toBe("loading");
  await act(async () => { d.settle("hello"); await d.promise; });
  expect(result.current).toEqual({ state: "ok", data: "hello", error: null });
});

test("an older answer landing after a newer request is dropped", async () => {
  const answers: Record<string, ReturnType<typeof deferred<string>>> = {
    a: deferred<string>(), b: deferred<string>(),
  };
  const { result, rerender } = renderHook(
    ({ key }) => useResource(() => answers[key].promise, [key]),
    { initialProps: { key: "a" } });
  rerender({ key: "b" });
  await act(async () => { answers.b.settle("B"); await answers.b.promise; });
  await act(async () => { answers.a.settle("A"); await answers.a.promise; });
  expect(result.current.data).toBe("B");
  expect(result.current.state).toBe("ok");
});

test("a failure is an error, and keeps the last answer", async () => {
  let fail = false;
  const { result, rerender } = renderHook(
    ({ n }) => useResource(
      () => (fail ? Promise.reject(new Error("refused")) : Promise.resolve(n)), [n]),
    { initialProps: { n: 1 } });
  await act(async () => {});
  expect(result.current).toEqual({ state: "ok", data: 1, error: null });
  fail = true;
  rerender({ n: 2 });
  await act(async () => {});
  expect(result.current).toEqual({ state: "error", data: 1, error: "refused" });
});

test("a deps change re-reads, and going back to null is unread again", async () => {
  const calls: number[] = [];
  const { result, rerender } = renderHook(
    ({ id }) => useResource(
      () => (id == null ? null : (calls.push(id), Promise.resolve(id * 10))), [id]),
    { initialProps: { id: 1 as number | null } });
  await act(async () => {});
  rerender({ id: 2 });
  await act(async () => {});
  expect(calls).toEqual([1, 2]);
  expect(result.current.data).toBe(20);
  rerender({ id: null });
  await act(async () => {});
  expect(result.current.state).toBe("unread");
  expect(calls).toEqual([1, 2]);
});
