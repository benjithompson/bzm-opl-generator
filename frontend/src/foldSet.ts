// Which of a list's sections are folded away, where several can be at once.
//
// The other fold on this page is openRow: one row open, and choosing another
// closes the first. This is the other shape -- a workspace folding says nothing
// about its neighbours, and "fold everything" is a thing to be able to ask for
// on an account with 54 of them.
//
// It holds the *folded* ones, so absent means open. That is the direction the
// default has to run: a workspace nobody has seen -- one arriving from a
// re-read of the account, or one a filter has just revealed -- opens, rather
// than being folded because it was not in a set built before it existed.
import { useState } from "react";

export interface FoldSet {
  folded: (id: number) => boolean;
  /** Fold this one, or unfold it if it already is. */
  toggle: (id: number) => void;
  /** Fold exactly these. The caller decides which -- see CapacityView, where
   *  it is every workspace on the account rather than every one on screen. */
  foldAll: (ids: number[]) => void;
  unfoldAll: () => void;
  /** Are all of these folded? False for none of them: a control that offered
   *  to unfold an empty page would be answering a question nobody asked. */
  allFolded: (ids: number[]) => boolean;
}

export function useFoldSet(): FoldSet {
  const [folded, setFolded] = useState<ReadonlySet<number>>(new Set());

  return {
    folded: (id) => folded.has(id),
    // Through the updater rather than off `folded`, so two toggles in one
    // batch do not both start from the set this render closed over.
    toggle: (id) => setFolded((cur) => {
      const next = new Set(cur);
      if (!next.delete(id)) next.add(id);
      return next;
    }),
    foldAll: (ids) => setFolded(new Set(ids)),
    unfoldAll: () => setFolded(new Set()),
    allFolded: (ids) => ids.length > 0 && ids.every((id) => folded.has(id)),
  };
}
