// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { useOpenRow } from "./openRow";

afterEach(() => { vi.useRealTimers(); });

test("a second toggle on the open row closes it", () => {
  const { result } = renderHook(() => useOpenRow());
  expect(result.current.open).toBeNull();

  act(() => result.current.toggle("a"));
  expect(result.current.open).toBe("a");
  // The whole point: the same header both opens and closes. Before this, the
  // location list could only be folded by choosing a different location.
  act(() => result.current.toggle("a"));
  expect(result.current.open).toBeNull();
});

test("the row on its way out stays on screen, and only until it has gone", () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useOpenRow());

  act(() => result.current.setOpen("a"));
  act(() => result.current.setOpen("b"));
  // Both, for the length of the transition: unmounting "a" in the frame "b"
  // opens is the jump the animation exists to remove.
  expect(result.current.shown("a")).toBe(true);
  expect(result.current.shown("b")).toBe(true);

  act(() => { vi.advanceTimersByTime(200); });
  expect(result.current.shown("a")).toBe(false);
  expect(result.current.shown("b")).toBe(true);
});

test("closing to nothing still animates the row out", () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useOpenRow());

  act(() => result.current.setOpen("a"));
  act(() => result.current.toggle("a"));
  // Closed, but not yet gone -- which is what makes a fold look like a fold
  // rather than a disappearance.
  expect(result.current.open).toBeNull();
  expect(result.current.shown("a")).toBe(true);

  act(() => { vi.advanceTimersByTime(200); });
  expect(result.current.shown("a")).toBe(false);
});
