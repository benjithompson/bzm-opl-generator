// The tables the server owns and the page only reads, fetched once on mount.
// Each keeps its "not read" value until it lands or if the read fails. Tables
// whose arrival changes other state are read in App instead.
import { useEffect, useState } from "react";

import {
  Api, BuildState, Functionality, PlaceholderSource, SlotMinimum, SvConstants,
} from "./api";
import { IgnoredByFormat } from "./formats";

interface ServedTables {
  svConst: SvConstants;
  /** What each format drops (see formats.ts); no entry means not read yet. */
  ignored: IgnoredByFormat;
  /** The variables the bundle writes itself. Empty until read, refusing nothing. */
  reservedEnv: Record<string, string | null>;
  /** Slot minimums per funcId. Empty until read, refusing nothing. */
  slotMinimums: Record<string, SlotMinimum>;
  /** Where each marked field's value comes from. Null, not `{}`, until read,
   *  because it is displayed rather than consulted. */
  placeholderSources: Record<string, PlaceholderSource> | null;
  /** The functionality vocabulary; empty until it lands, which hides nothing. */
  functionalities: Functionality[];
  /** Null until read; see build.ts. */
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
