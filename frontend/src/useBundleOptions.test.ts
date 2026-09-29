// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { merged, useBundleOptions } from "./useBundleOptions";

afterEach(cleanup);

test("a patch that changes nothing keeps the object", () => {
  const o = { namespace: "blazemeter", proxy: null };
  expect(merged(o, { namespace: "blazemeter" })).toBe(o);
  expect(merged(o, {})).toBe(o);
  expect(merged(o, null)).toBe(o);
  expect(merged(o, { namespace: "other" })).toEqual({ namespace: "other", proxy: null });
});

test("set and patch keep the options identity when the value is already there", () => {
  const { result } = renderHook(() => useBundleOptions());
  const before = result.current.options;
  act(() => { result.current.set("namespace", "blazemeter"); });
  expect(result.current.options).toBe(before);
  act(() => { result.current.patch({ namespace: "x" }); });
  expect(result.current.options).not.toBe(before);
  expect(result.current.options.namespace).toBe("x");
});

test("a proxy with every field emptied is no proxy at all", () => {
  const { result } = renderHook(() => useBundleOptions());
  act(() => { result.current.setProxy("http", "http://p:3128"); });
  expect(result.current.options.proxy).toEqual({ http: "http://p:3128" });
  act(() => { result.current.setProxy("http", ""); });
  expect(result.current.options.proxy).toBeNull();
});

test("defaults fill only what is not set", () => {
  const { result } = renderHook(() => useBundleOptions());
  act(() => { result.current.applyDefaults({ namespace: "d", output_format: "manifests" }); });
  expect(result.current.options).toEqual({ namespace: "blazemeter", output_format: "manifests" });
});
