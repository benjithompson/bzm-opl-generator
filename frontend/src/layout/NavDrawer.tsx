// The left drawer: which of the two views is open, with the key and account at
// its foot. It collapses to a rail; one corner holds both the open and the
// close control.
import { ReactNode } from "react";

export type ViewId = "flow" | "capacity";

interface NavItem {
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

/** Plain inline shapes: a document for the bundle, bars for the rollup. */
const NAV: NavItem[] = [
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
  /** Without a key the capacity view is out of reach, and says how to fix it. */
  connected: boolean;
  /** The key and account, at the drawer's foot. */
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
          <span className="text-2xs font-semibold uppercase tracking-wide
                           text-slate-400 grow">
            Views
          </span>
        )}
        <button type="button"
          onClick={() => props.setOpen(!open)}
          aria-expanded={open}
          aria-label={open ? "Collapse the menu" : "Open the menu"}
          title={open ? "Collapse" : "Menu"}
          className={"rounded-md text-slate-500 hover:text-slate-900 hover:bg-slate-100 "
            + "flex items-center justify-center w-8 h-8 "
            + (open ? "border border-slate-300" : "")}>
          {open ? (
            // Pointing at the edge it collapses towards.
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
            <button type="button" key={item.id} onClick={() => !off && props.setView(item.id)}
              aria-current={on ? "page" : undefined}
              disabled={off}
              // The label is the tooltip while collapsed.
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

      {/* Pinned to the bottom, so the key stays put. */}
      {props.footer && (
        <div className={"mt-auto border-t border-slate-200 space-y-1.5 "
          + (open ? "p-2" : "p-1.5")}>
          {props.footer}
        </div>
      )}
    </nav>
  );
}
