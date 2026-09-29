// The tables the server owns and the page only reads: fetched once on mount,
// never written back, and each empty until it lands.
//
// Out of App because nothing about them is App's to decide -- they are the
// generator's vocabulary, served so the page never keeps a copy (see the
// IGNORED_BY_FORMAT and RESERVED_ENV rules in CLAUDE.md). App still owns them
// in the sense that matters: the hook is called there and the values are
// handed down as props like every other piece of domain state.
//
// The tables whose arrival *changes* other state stay in App's mount effect:
// the option defaults merge into `options`, the sizing models seed the saved
// sizings, and the funcId vocabulary is re-read once an account is chosen.
import { useEffect, useState } from "react";

import {
  Api, BuildState, Functionality, PlaceholderSource, SlotMinimum, SvConstants,
} from "./api";
import { IgnoredByFormat } from "./formats";

export interface ServedTables {
  svConst: SvConstants;
  /** What each format drops, from the generator (see formats.ts). No entry
   *  for a format is nothing having been read for it -- which is every format
   *  until this lands -- and a format whose entry is `{}` drops nothing, which
   *  is an answer. Both show every option: a field too many beats hiding a
   *  required one on a guess. */
  ignored: IgnoredByFormat;
  /** The environment variables the bundle writes for itself, which the env
   *  area refuses. Empty until read, and nothing is refused until then:
   *  generate() refuses authoritatively either way, and a name rejected on a
   *  guess is the worse half of being wrong. */
  reservedEnv: Record<string, string | null>;
  /** What BlazeMeter requires of a new location's `slots` (#159). Empty the
   *  same way and meaning the same thing: core.create_location refuses
   *  authoritatively either way. */
  slotMinimums: Record<string, SlotMinimum>;
  /** Where the value for each marked field comes from. `null` rather than
   *  `{}`, and that is the difference from the tables above: they are
   *  *consulted*, so empty honestly means "nothing refused yet", while this
   *  one is *displayed*, and a row with no sentence must read as one nobody
   *  read rather than one the generator declined to give. */
  placeholderSources: Record<string, PlaceholderSource> | null;
  /** The functionality vocabulary; empty until it lands, which hides nothing. */
  functionalities: Functionality[];
  /** Null until read; see build.ts for the four answers. Asked beside the
   *  other tables for their sake: a page built before the code behind it makes
   *  every one of them 404, which reads as "not read yet" (#224). */
  build: BuildState | null;
}

export function useServedTables(api: Api): ServedTables {
  const [svConst, setSvConst] = useState<SvConstants>(
    { func_ids: [], ingress_types: [], backends: {} });
  const [ignored, setIgnored] = useState<IgnoredByFormat>({});
  const [reservedEnv, setReservedEnv] = useState<Record<string, string | null>>({});
  const [slotMinimums, setSlotMinimums] = useState<Record<string, SlotMinimum>>({});
  const [placeholderSources, setPlaceholderSources] =
    useState<Record<string, PlaceholderSource> | null>(null);
  const [functionalities, setFunctionalities] = useState<Functionality[]>([]);
  const [build, setBuild] = useState<BuildState | null>(null);

  useEffect(() => {
    // A refusal leaves each at its "not read" value, which every reader
    // already treats as such.
    api.svConstants().then(setSvConst).catch(() => {});
    api.ignoredOptions().then(setIgnored).catch(() => {});
    api.build().then(setBuild).catch(() => {});
    api.reservedEnv().then(setReservedEnv).catch(() => {});
    api.placeholders().then(setPlaceholderSources).catch(() => {});
    api.slotMinimums().then(setSlotMinimums).catch(() => {});
    api.functionalities().then(setFunctionalities).catch(() => {});
  }, [api]);

  return { svConst, ignored, reservedEnv, slotMinimums, placeholderSources,
           functionalities, build };
}
