// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { fakeApi } from "./fakeApi";
import { IGNORED_BY_FORMAT } from "./fixtures";
import { useServedTables } from "./useServedTables";

test("a table that lands is served, and one that is refused stays unread", async () => {
  // Nothing but the ignored-options table answers: every other route is the
  // fake's rejection, which must leave each at its own "not read" value --
  // null where the table is displayed, empty where it is consulted.
  const { result } = renderHook(() => useServedTables(fakeApi({
    ignoredOptions: async () => IGNORED_BY_FORMAT,
  })));
  await waitFor(() => expect(result.current.ignored).toEqual(IGNORED_BY_FORMAT));
  expect(result.current.placeholderSources).toBeNull();
  expect(result.current.build).toBeNull();
  expect(result.current.reservedEnv).toEqual({});
  expect(result.current.functionalities).toEqual([]);
});
