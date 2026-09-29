// Which of a list's sections are folded, where several can be. It holds the
// folded ones, so a section nobody has seen yet is open.
import { useState } from "react";

interface FoldSet {
  folded: (id: number) => boolean;
  /** Fold this one, or unfold it if it already is. */
  toggle: (id: number) => void;
  /** Fold exactly these. */
  foldAll: (ids: number[]) => void;
  unfoldAll: () => void;
  /** Are all of these folded? False for an empty list. */
  allFolded: (ids: number[]) => boolean;
}

export function useFoldSet(): FoldSet {
  const [folded, setFolded] = useState<ReadonlySet<number>>(new Set());

  return {
    folded: (id) => folded.has(id),
    // Through the updater, so two toggles in one batch both apply.
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
