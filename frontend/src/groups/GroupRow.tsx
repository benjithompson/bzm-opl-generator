import { ReactNode } from "react";
import { Switch } from "../components";
import { OptionGroup } from "../optionGroups";

/** One row of the option-group list: the switch, the title and hint, and the
 *  group's body while it is on. `required` (the location demands it) picks the
 *  required hint; `declined` is that demand switched off, and says what it costs. */
export function GroupRow(props: {
  group: OptionGroup;
  on: boolean;
  required?: boolean;
  /** Required by the location and switched off anyway; never with `required`. */
  declined?: boolean;
  /** Which functionalities this group belongs to. */
  applies: string;
  onFlip: (on: boolean) => void;
  children: ReactNode;
}) {
  const { group, on, required, declined, applies } = props;
  const hint = required ? (group.requiredHint ?? group.hint)
    : declined ? (group.declinedHint ?? group.hint)
    : group.hint;
  return (
    <div className="px-3 py-2.5">
      <div className="flex items-center gap-3">
        {/* Named by the group, since the title is a sibling, not a <label>. */}
        <Switch on={on} onChange={props.onFlip} label={group.title} />
        <div className="min-w-0 grow">
          <p className={`text-sm font-medium ${on ? "text-slate-900" : "text-slate-500"}`}>
            {group.title}
            {required && (
              <span className="ml-2 text-3xs font-semibold uppercase tracking-wide text-bzm">
                required
              </span>
            )}
            {declined && (
              <span className="ml-2 text-3xs font-semibold uppercase tracking-wide text-amber-600">
                declined
              </span>
            )}
            {applies && (
              <span className="ml-2 text-3xs font-medium tracking-wide rounded bg-slate-100 text-slate-500 px-1.5 py-0.5 align-middle">
                {applies}
              </span>
            )}
          </p>
          <p className={`text-2xs truncate ${declined ? "text-amber-700" : "text-slate-400"}`}>
            {hint}
          </p>
        </div>
      </div>
      {/* Off hides the fields; the group's disable() clears their options. */}
      {on && <div className="mt-3 pl-12 space-y-2">{props.children}</div>}
    </div>
  );
}
