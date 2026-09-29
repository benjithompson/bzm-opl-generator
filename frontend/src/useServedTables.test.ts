// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { fakeApi } from "./fakeApi";
import { IGNORED_BY_FORMAT } from "./fixtures";
import { useServedTables } from "./useServedTables";

test("a table that lands is served, and one that is refused stays unread", async () => {
  // Only one table answers; each other stays at its "not read" value (null
  // where displayed, empty where consulted).
  const { result } = renderHook(() => useServedTables(fakeApi({
    ignoredOptions: async () => IGNORED_BY_FORMAT,
  })));
  await waitFor(() => expect(result.current.ignored).toEqual(IGNORED_BY_FORMAT));
  expect(result.current.placeholderSources).toBeNull();
  expect(result.current.build).toBeNull();
  expect(result.current.reservedEnv).toEqual({});
  expect(result.current.functionalities).toEqual([]);
});
