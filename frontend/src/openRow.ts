// Which row of a list is open, separate from which is selected: folding a row
// must not change what the bundle is for. A closing row stays mounted until its
// animation ends.
import { useEffect, useRef, useState } from "react";

/** How long a closing row stays mounted: the 180ms animation plus a frame. */
const EXIT_MS = 200;

interface OpenRow {
  /** The row that is open, or null. */
  open: string | null;
  /** Open one, or none, where something other than a click decides. */
  setOpen: (id: string | null) => void;
  /** Open `id`, or close it if it is the one already open. */
  toggle: (id: string) => void;
  /** Is this row's body on screen? True for the open row, and for the one
   *  still animating shut. */
  shown: (id: string) => boolean;
}

export function useOpenRow(): OpenRow {
  const [open, setOpen] = useState<string | null>(null);
  const [closing, setClosing] = useState<string | null>(null);
  const was = useRef<string | null>(null);

  useEffect(() => {
    const prev = was.current;
    was.current = open;
    if (!prev || prev === open) return;
    setClosing(prev);
    const t = setTimeout(() => setClosing(null), EXIT_MS);
    return () => clearTimeout(t);
  }, [open]);

  return {
    open,
    setOpen,
    toggle: (id) => setOpen((cur) => (cur === id ? null : id)),
    shown: (id) => open === id || closing === id,
  };
}
