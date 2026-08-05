import { ReactNode } from "react";
import { Switch } from "../components";
import { OptionGroup } from "../optionGroups";

/** One row of the option-group list: the switch, the declared title and hint,
 *  and the group's own body once it is on.
 *
 *  `required` is the one thing about a row that the options cannot say -- an SV
 *  location needs its group whatever is configured -- so it arrives as a prop
 *  and picks the declaration's `requiredHint`. `declined` is that same demand
 *  answered no: the row keeps saying which location it is, and says what the
 *  bundle gives up, instead of reading like a group nobody ever needed.
 *
 *  `applies` is the group's attribution, rendered on every row including the
 *  ones that belong to no functionality: with only some rows badged, an unbadged row
 *  reads as an oversight rather than as "this applies to any deployment". It
 *  arrives resolved (appliesTo) because the labels are served, not declared.
 */
export function GroupRow(props: {
  group: OptionGroup;
  on: boolean;
  required?: boolean;
  /** Required by the location, and switched off anyway. Never true with
   *  `required`: they are the two answers to one question. */
  declined?: boolean;
  /** Which functionalities this group belongs to, always shown -- every row is
   *  attributed, so no option is on screen without saying why. */
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
        {/* Named by the group, because the title beside it is a sibling rather
            than a <label>: without this the only way to reach a particular
            row's switch is to navigate the markup around its title. */}
        <Switch on={on} onChange={props.onFlip} label={group.title} />
        <div className="min-w-0 grow">
          <p className={`text-sm font-medium ${on ? "text-slate-900" : "text-slate-500"}`}>
            {group.title}
            {required && (
              <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-bzm">
                required
              </span>
            )}
            {declined && (
              <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-amber-600">
                declined
              </span>
            )}
            {applies && (
              <span className="ml-2 text-[10px] font-medium tracking-wide rounded bg-slate-100 text-slate-500 px-1.5 py-0.5 align-middle">
                {applies}
              </span>
            )}
          </p>
          <p className={`text-[11px] truncate ${declined ? "text-amber-700" : "text-slate-400"}`}>
            {hint}
          </p>
        </div>
      </div>
      {/* OFF hides the fields; the group's disable is what wipes their
          options, so nothing hidden ever reaches the manifests. */}
      {on && <div className="mt-3 pl-12 space-y-2">{props.children}</div>}
    </div>
  );
}
