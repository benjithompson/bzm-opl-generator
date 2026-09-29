import { useCallback, useState } from "react";
import { Options } from "./api";
import {
  allGroupsOff, CaMode, caModeOf, caModePatch, GROUP_BY_ID, GroupFlags, GroupId,
} from "./optionGroups";

/** `o` with `p` applied, or `o` itself when every key already holds its value.
 *
 *  Keeping the identity matters: the preview re-generates whenever the options
 *  object changes, so a write that changes nothing must not look like one. */
export function merged(o: Options, p: Options | null | undefined): Options {
  if (!p) return o;
  return Object.keys(p).some((k) => o[k] !== p[k]) ? { ...o, ...p } : o;
}

/** The generator options being edited, which option groups are switched on,
 *  and the writes the form makes to them. */
export function useBundleOptions() {
  const [options, setOptions] = useState<Options>({ namespace: "blazemeter" });
  // The generator's defaults, for filling a profile and for leaving them out of one.
  const [defaults, setDefaultsState] = useState<Options>({});
  const [grpOn, setGrpOn] = useState<GroupFlags>(allGroupsOff);

  const patch = useCallback(
    (p: Options | null | undefined) => setOptions((o) => merged(o, p)), []);
  const set = useCallback((k: string, v: unknown) => patch({ [k]: v }), [patch]);

  /** Fill in whatever is not set yet from the generator's defaults. */
  const applyDefaults = useCallback((d: Options) => {
    setDefaultsState(d);
    setOptions((o) => ({ ...d, ...o }));
  }, []);

  /** Switching a group off clears its options, so nothing hidden reaches the
   *  bundle. `required` lets a group the location demands record the refusal. */
  const flipGroup = useCallback((id: GroupId, on: boolean, required: boolean) => {
    setGrpOn((g) => ({ ...g, [id]: on }));
    const group = GROUP_BY_ID[id];
    setOptions((o) => merged(o, on ? group.enable(o) : group.disable(o, required)));
  }, []);

  // CA trust is one-of, derived from the options so the radios cannot disagree.
  const caMode: CaMode = caModeOf(options);
  const setCaMode = useCallback(
    (m: CaMode) => setOptions((o) => merged(o, caModePatch(o, m))), []);

  const proxy = (options.proxy ?? {}) as Record<string, string | undefined>;
  const setProxy = useCallback((k: string, v: string) => setOptions((o) => {
    const p = { ...(o.proxy ?? {}) as Record<string, string | undefined>,
                [k]: v || undefined };
    return { ...o, proxy: Object.values(p).some(Boolean) ? p : null };
  }), []);

  /** Replace the options with a profile file's, over the defaults. */
  const importProfile = useCallback((file: File, onError: (msg: string) => void) => {
    file.text().then((t) => setOptions({ ...defaults, ...JSON.parse(t) }))
      .catch(() => onError("could not parse profile JSON"));
  }, [defaults]);

  /** Download the options that differ from the defaults, minus the identity
   *  and the credential. */
  const exportProfile = useCallback(() => {
    const drop = new Set(["auth_token", "ship_id"]);
    const clean = Object.fromEntries(Object.entries(options)
      .filter(([k, v]) => !drop.has(k) && v !== defaults[k]));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(clean, null, 2)],
      { type: "application/json" }));
    a.download = "bzm-opl-profile.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }, [options, defaults]);

  return {
    options, setOptions, patch, set, applyDefaults,
    grpOn, setGrpOn, flipGroup,
    caMode, setCaMode, proxy, setProxy,
    importProfile, exportProfile,
  };
}
