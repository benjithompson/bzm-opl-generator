// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { expect, test } from "vitest";

import { useFoldSet } from "./foldSet";

test("everything is open until something is folded", () => {
  const { result } = renderHook(() => useFoldSet());
  // Absent is open, which is what makes an id nobody has seen yet -- a
  // workspace arriving from a re-read of the account -- arrive open.
  expect(result.current.folded(1)).toBe(false);
  expect(result.current.folded(99)).toBe(false);
});

test("a header folds its own row and nothing else", () => {
  const { result } = renderHook(() => useFoldSet());

  act(() => result.current.toggle(1));
  expect(result.current.folded(1)).toBe(true);
  expect(result.current.folded(2)).toBe(false);

  act(() => result.current.toggle(1));
  expect(result.current.folded(1)).toBe(false);
});

test("fold-all takes the ids it is given, and unfold-all takes them all back", () => {
  const { result } = renderHook(() => useFoldSet());

  act(() => result.current.foldAll([1, 2, 3]));
  expect([1, 2, 3].every(result.current.folded)).toBe(true);

  // Unfolding is not "the ones I was shown": a filter narrows what is on
  // screen, and leaving a workspace folded because it was hidden when the
  // button was pressed is a state nobody can see to undo.
  act(() => result.current.unfoldAll());
  expect([1, 2, 3].some(result.current.folded)).toBe(false);
});

test("allFolded is about the rows asked after, and no rows is not all folded", () => {
  const { result } = renderHook(() => useFoldSet());
  // The control reads this to decide which way it goes. An empty list would
  // make it offer to unfold a page with nothing on it.
  expect(result.current.allFolded([])).toBe(false);

  act(() => result.current.foldAll([1, 2]));
  expect(result.current.allFolded([1, 2])).toBe(true);
  // A third one, folded by nothing: the button has something left to do.
  expect(result.current.allFolded([1, 2, 3])).toBe(false);
});
