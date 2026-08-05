// The left drawer: which of the two things this page is.
//
// The views were buttons in the header, competing with the product name and the
// connected account for the same line. A drawer is where an app this shape
// keeps them, and it collapses to a rail so the width is the reader's choice
// rather than the layout's.
//
// There were three. "Plan capacity" is now the first card of Generate's first
// step: it was a view because it needs no account, but sitting beside the flow
// made the first question look like an alternative to it, and its answer had to
// be carried across by hand. Needing no account is not a reason to be
// elsewhere -- it is a reason to be first.
//
// The controls follow the convention rather than inventing one: a hamburger
// opens it, and an outlined left arrow closes it. Both live in the same corner,
// so the thing that opened it is the thing that closes it -- a rail whose open
// control is at the top and whose close control is somewhere else is two
// controls to learn.
import { ReactNode } from "react";

export type ViewId = "flow" | "capacity";

// Neither item carries a hint any more, and the rail renders none. Generate
// lost its first ("The Generate rail says nothing step 1 does not"), and
// Account capacity's -- "what this account can generate" -- said in the chrome
// what the view says at the top of itself, one line above a total the same
// sentence describes. A label a whole view is named after does not need a
// second sentence in the furniture beside it.
export interface NavItem {
  id: ViewId;
  label: string;
  icon: ReactNode;
}

const stroke = {
  fill: "none", stroke: "currentColor", strokeWidth: 1.75,
  strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
};

const Icon = ({ d }: { d: string }) => (
  <svg viewBox="0 0 20 20" className="w-4 h-4 shrink-0" aria-hidden="true" {...stroke}>
    <path d={d} />
  </svg>
);

/** Deliberately plain shapes: a document for the bundle, bars for the account
 *  rollup. No icon set is worth a dependency for two glyphs. */
export const NAV: NavItem[] = [
  { id: "flow", label: "Generate",
    icon: <Icon d="M5 2.5h6l4 4v11h-10zM11 2.5v4h4M7.5 11h5M7.5 14h5" /> },
  { id: "capacity", label: "Account capacity",
    icon: <Icon d="M3 16.5h14M6 16.5v-5M10 16.5v-9M14 16.5v-3" /> },
];

export function NavDrawer(props: {
  view: ViewId;
  setView: (v: ViewId) => void;
  open: boolean;
  setOpen: (v: boolean) => void;
  /** Without a key there is no account to roll up, so that view is out of
   *  reach rather than empty -- and it says which button fixes that. */
  connected: boolean;
  /** The key and the account, at the foot of the drawer. Session-wide, like the
   *  views above them and unlike anything inside a step -- and down here rather
   *  than in the header because that is where an application this shape keeps
   *  the identity it is working under. */
  footer?: ReactNode;
}) {
  const { open } = props;
  return (
    <nav aria-label="Views"
      className={"shrink-0 border-r border-slate-200 bg-white flex flex-col "
        + "transition-[width] duration-200 ease-out "
        + (open ? "w-52" : "w-14")}>
      <div className={"flex items-center h-12 border-b border-slate-200 "
        + (open ? "px-3 gap-2" : "justify-center")}>
        {open && (
          <span className="text-[11px] font-semibold uppercase tracking-wide
                           text-slate-400 grow">
            Views
          </span>
        )}
        <button
          onClick={() => props.setOpen(!open)}
          aria-expanded={open}
          aria-label={open ? "Collapse the menu" : "Open the menu"}
          title={open ? "Collapse" : "Menu"}
          className={"rounded-md text-slate-500 hover:text-slate-900 hover:bg-slate-100 "
            + "flex items-center justify-center w-8 h-8 "
            + (open ? "border border-slate-300" : "")}>
          {open ? (
            // Outlined, and pointing at the edge it collapses towards.
            <svg viewBox="0 0 20 20" className="w-4 h-4" {...stroke}>
              <path d="M12 5l-5 5 5 5" />
            </svg>
          ) : (
            <svg viewBox="0 0 20 20" className="w-4 h-4" {...stroke}>
              <path d="M3.5 6h13M3.5 10h13M3.5 14h13" />
            </svg>
          )}
        </button>
      </div>

      <div className="p-2 space-y-1">
        {NAV.map((item) => {
          const on = props.view === item.id;
          const off = item.id === "capacity" && !props.connected;
          return (
            <button key={item.id} onClick={() => !off && props.setView(item.id)}
              aria-current={on ? "page" : undefined}
              disabled={off}
              // The label is the tooltip while collapsed, so the rail is
              // usable without opening it first.
              title={off ? "connect an account first — the key at the foot of this menu"
                : item.label}
              className={"w-full flex items-center gap-2.5 rounded-md text-left "
                + "transition-colors h-9 "
                + (open ? "px-2.5 " : "justify-center px-0 ")
                + (on ? "bg-bzm text-white"
                  : off ? "text-slate-300 cursor-not-allowed"
                    : "text-slate-600 hover:bg-slate-100")}>
              {item.icon}
              {open && (
                <span className="text-sm font-medium truncate">{item.label}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Pinned to the bottom, so the key does not move when the drawer
          collapses or when the view changes. It used to share that job with a
          per-view hint above it, both on `mt-auto`. */}
      {props.footer && (
        <div className={"mt-auto border-t border-slate-200 space-y-1.5 "
          + (open ? "p-2" : "p-1.5")}>
          {props.footer}
        </div>
      )}
    </nav>
  );
}
