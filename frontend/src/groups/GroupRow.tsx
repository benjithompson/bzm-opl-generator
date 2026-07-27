import { ReactNode } from "react";
import { Switch } from "../components";
import { OptionGroup } from "../optionGroups";

/** One row of the option-group list: the switch, the declared title and hint,
 *  and the group's own body once it is on.
 *
 *  `required` is the one thing about a row that the options cannot say -- an SV
 *  location needs its group whatever is configured -- so it arrives as a prop
 *  and picks the declaration's `requiredHint`.
 */
export function GroupRow(props: {
  group: OptionGroup;
  on: boolean;
  required?: boolean;
  onFlip: (on: boolean) => void;
  children: ReactNode;
}) {
  const { group, on, required } = props;
  return (
    <div className="px-3 py-2.5">
      <div className="flex items-center gap-3">
        <Switch on={on} onChange={props.onFlip} />
        <div className="min-w-0">
          <p className={`text-sm font-medium ${on ? "text-slate-900" : "text-slate-500"}`}>
            {group.title}
            {required && (
              <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-bzm">
                required
              </span>
            )}
          </p>
          <p className="text-[11px] text-slate-400 truncate">
            {required && group.requiredHint ? group.requiredHint : group.hint}
          </p>
        </div>
      </div>
      {/* OFF hides the fields; the group's disable is what wipes their
          options, so nothing hidden ever reaches the manifests. */}
      {on && <div className="mt-3 pl-12 space-y-2">{props.children}</div>}
    </div>
  );
}
