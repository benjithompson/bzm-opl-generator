// Which row of a list is open, for the two lists that open one.
//
// Open is not selected, and that is the whole point of the separation: the
// agent list had it already -- closing a row must not un-choose the agent the
// bundle is for -- and the location list did not, so its rows could only be
// folded by choosing a *different* location, which changes what is being
// generated in order to hide some text.
//
// The second thing it carries is the row on its way out. A row that unmounts
// the moment it closes collapses in one frame while its replacement animates
// open, which is the jump the animation exists to remove; so the one that is
// going stays on screen for as long as the transition takes.
import { useEffect, useRef, useState } from "react";

/** How long a closing row stays mounted. The rows animate at 180ms; this is
 *  that plus a frame, and it is a ceiling rather than a schedule -- nothing is
 *  timed against it, the row is simply gone by the time it fires. */
const EXIT_MS = 200;

export interface OpenRow {
  /** The row that is open, or null. */
  open: string | null;
  /** Open one, or none. Used where something other than a click decides --
   *  picking a location, or the lone-agent auto-pick. */
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
