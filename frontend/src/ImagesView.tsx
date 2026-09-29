// The container images a corporate registry has to hold: each reference, what
// it is for, which functionality pulls it and when, and what the public
// registry says about it. Everything shown is a served row; the view only
// words it, groups it and exports it.
import { useState } from "react";

import { ImageRow, ImagesAnswer } from "./api";
import {
  Button, Callout, cardCls, ErrorMsg, SegmentedControl, Spinner,
} from "./components";
import {
  catalogueLine, digestText, driftNote, fileStem, groupByCategory, LabelOf,
  listNotice, newestNotice,
  refsText, registryNotice, resolvedText, sizeText, SOURCE_TEXT, sourceHeading, TagNote,
  tagNotes, toCsv, toMarkdown,
} from "./images";
import { plural } from "./text";

/** Why the catalogue is on screen rather than a location's versions. */
export type CatalogueReason = "disconnected" | "no-location" | "manual";

const CATALOGUE_HINT: Record<CatalogueReason, string> = {
  disconnected: "Connect an account and choose a location for the versions"
    + " your location uses.",
  "no-location": "Choose a location under Generate for the versions it uses.",
  manual: "Manual entry reads nothing from an account. Connect one for the"
    + " versions your location uses.",
};

/** Hand `text` to the browser as a file. */
function saveText(text: string, filename: string, type: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function ImagesView(props: {
  answer: ImagesAnswer | null;
  busy: boolean;
  error: string | null;
  /** Every image of the location's functionalities, or only the required ones. */
  all: boolean;
  setAll: (v: boolean) => void;
  labelOf: LabelOf;
  /** The bundle's Registry option, or null where it is not set; it fills the
   *  check command. */
  registry: string | null;
  /** Why the catalogue is what is asked for, or null where a location is. */
  catalogueReason: CatalogueReason | null;
}) {
  const { answer } = props;
  // What the last copy did, keyed by what was copied, so one button says so.
  const [copied, setCopied] = useState<string | null>(null);
  const [copyErr, setCopyErr] = useState<string | null>(null);

  const copy = (what: string, text: string) => {
    setCopyErr(null);
    navigator.clipboard.writeText(text).then(() => {
      setCopied(what);
      window.setTimeout(() => setCopied((c) => (c === what ? null : c)), 2000);
    }).catch(() => setCopyErr("could not write to the clipboard"));
  };

  const drift = driftNote(props.registry);
  const notices = answer
    ? [newestNotice(answer), listNotice(answer), registryNotice(answer)]
      .filter((n): n is string => !!n)
    : [];
  const showRequired = !!answer?.images.some((r) => r.required !== null);

  return (
    <div className="space-y-4">
      <div className={cardCls}>
        <div className="flex items-start gap-3 flex-wrap">
          <div className="grow min-w-0">
            <h2 className="font-semibold text-slate-900 leading-tight break-words">
              {answer ? sourceHeading(answer) : "Images"}
            </h2>
            {answer?.source === "catalogue" && (
              <p className="text-xs text-slate-500 mt-0.5">
                {catalogueLine(answer)}
                {props.catalogueReason && <> {CATALOGUE_HINT[props.catalogueReason]}</>}
              </p>
            )}
            {answer?.source === "location" && (
              <p className="text-xs text-slate-500 mt-0.5">
                The versions crane will ask for, read from BlazeMeter for this
                location.
              </p>
            )}
          </div>
          {props.busy && (
            <span role="status" className="flex items-center gap-1.5 text-xs text-slate-500">
              <Spinner className="text-bzm" /> reading the images…
            </span>
          )}
        </div>

        {/* The catalogue is one list: nothing in it is required of a location. */}
        {props.catalogueReason === null ? (
          <SegmentedControl label="Show"
            value={props.all ? "all" : "required"}
            onChange={(v) => props.setAll(v === "all")}
            options={[
              { value: "required", label: "Required only",
                hint: "What this location needs to run." },
              { value: "all", label: "All",
                hint: "Every image its functionalities can pull." },
            ]} />
        ) : (
          <p className="text-xs text-slate-500">
            The catalogue lists every image it knows, for every functionality.
            A location&apos;s list also says which images that location requires.
          </p>
        )}

        {answer && answer.images.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <Button kind="ghost"
              onClick={() => copy("all", refsText(answer.images))}>
              {copied === "all" ? "Copied" : "Copy"}
            </Button>
            <Button kind="ghost"
              onClick={() => saveText(toCsv(answer.images, props.labelOf),
                                      `${fileStem(answer)}.csv`, "text/csv")}>
              CSV
            </Button>
            <Button kind="ghost"
              onClick={() => saveText(
                toMarkdown(answer, props.labelOf, props.registry),
                `${fileStem(answer)}.md`, "text/markdown")}>
              Markdown
            </Button>
            <span className="text-2xs text-slate-500">
              Copy puts every reference on the clipboard, one per line. CSV and
              Markdown download the {plural(answer.images.length, "row")} shown,
              made in this page with no further request.
            </span>
          </div>
        )}
        <ErrorMsg msg={copyErr} />
      </div>

      <ErrorMsg msg={props.error} className="text-sm" />

      {notices.map((n) => (
        <Callout key={n} tone="amber" className="text-xs">
          <p role="note">{n}</p>
        </Callout>
      ))}

      {answer && answer.images.length === 0 && (
        <p className="text-sm text-slate-500">
          {props.all || props.catalogueReason !== null ? "No images are listed."
            : "No images are listed as required. Choose All to see every image"
              + " its functionalities can pull."}
        </p>
      )}

      {answer && groupByCategory(answer.images).map((g) => (
        <CategoryTable key={g.category} category={g.category} rows={g.rows}
          labelOf={props.labelOf} showRequired={showRequired}
          copied={copied} copy={copy} />
      ))}

      {/* The mirror's own names are the generator's to compose, so they are not
          shown here; the command checks them against the bundle's registry. */}
      <p className="text-xs text-slate-600">
        {drift.text} <code className="font-mono bg-slate-100 rounded px-1 break-all">
          {drift.command}</code>
      </p>
    </div>
  );
}

const TAG_TONE: Record<TagNote["kind"], string> = {
  pinned: "text-slate-500",
  mutable: "text-amber-700",
  newer: "text-sky-700 font-medium",
};

function CategoryTable(props: {
  category: string;
  rows: ImageRow[];
  labelOf: LabelOf;
  showRequired: boolean;
  copied: string | null;
  copy: (what: string, text: string) => void;
}) {
  const { rows } = props;
  return (
    <section className="bg-white border border-slate-200 rounded-lg overflow-hidden"
      aria-label={props.category}>
      <h3 className="px-3 py-2 text-sm font-semibold text-slate-800 bg-slate-50
                     border-b border-slate-200">
        {props.category}{" "}
        <span className="text-xs font-normal text-slate-400">
          {plural(rows.length, "image")}
        </span>
      </h3>
      {/* The table scrolls inside its card at narrow widths, not the page. */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs min-w-[44rem]">
          <caption className="sr-only">{props.category} images</caption>
          <thead className="text-slate-500">
            <tr className="border-b border-slate-100">
              <th scope="col" className="text-left font-medium px-3 py-1.5">image</th>
              <th scope="col" className="text-left font-medium px-2">purpose</th>
              <th scope="col" className="text-left font-medium px-2">pulled</th>
              <th scope="col" className="text-right font-medium px-2">size</th>
              <th scope="col" className="text-left font-medium px-2">digest</th>
              <th scope="col" className="text-left font-medium px-2">tag</th>
              {props.showRequired && (
                <th scope="col" className="text-left font-medium px-3">required</th>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <Row key={`${r.key}|${r.ref}`} r={r} i={i} {...props} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Row(props: {
  r: ImageRow; i: number; labelOf: LabelOf; showRequired: boolean;
  copied: string | null; copy: (what: string, text: string) => void;
}) {
  const { r } = props;
  const unread = r.registry_state === "unread";
  // Unread is said in amber, with the reason on hover; never blank.
  const gapCls = unread ? "text-amber-700" : "text-slate-400";
  const refKey = `ref:${r.ref}`;
  const digestKey = `digest:${r.ref}`;
  const resolved = resolvedText(r);
  return (
    <tr className={"align-top " + (props.i % 2 ? "bg-slate-50/60" : "")}>
      <th scope="row" className="text-left font-normal px-3 py-1.5">
        <span className="flex items-start gap-1.5">
          <code className="font-mono text-slate-800 break-all">{r.ref}</code>
          <button type="button" aria-label={`Copy ${r.ref}`}
            onClick={() => props.copy(refKey, r.ref)}
            className="shrink-0 text-2xs text-bzm hover:underline">
            {props.copied === refKey ? "Copied" : "Copy"}
          </button>
        </span>
        <span className="flex flex-wrap gap-1 mt-1">
          {r.functionalities.map((f) => {
            const label = props.labelOf(f);
            // A funcId nothing served names is shown as the funcId, in code
            // type, so it never passes for BlazeMeter's name.
            return (
              <span key={f} title={label ? f : "the funcId; connect an account"
                + " for BlazeMeter's name for it"}
                className={"text-3xs rounded bg-slate-100 text-slate-600 px-1.5 py-0.5"
                  + (label ? "" : " font-mono")}>
                {label ?? f}
              </span>
            );
          })}
        </span>
        <span className="block text-3xs text-slate-400 mt-0.5">
          tag from {SOURCE_TEXT[r.source] ?? r.source}
        </span>
      </th>
      <td className="px-2 py-1.5 text-slate-700">
        {r.purpose}
        {!r.verified && (
          <span className="text-slate-400"
            title="inferred from the image, not observed on a running agent">
            {" "}(inferred)
          </span>
        )}
      </td>
      <td className="px-2 py-1.5 text-slate-600">{r.pulled_when}</td>
      <td className={"px-2 py-1.5 text-right tabular-nums whitespace-nowrap "
        + (r.size_mb == null ? gapCls : "")}
        title={unread ? r.registry_detail ?? undefined : undefined}>
        {sizeText(r)}
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        {r.registry_state === "read" && r.digest ? (
          <button type="button" title={r.digest}
            aria-label={`Copy the digest of ${r.ref}`}
            onClick={() => props.copy(digestKey, r.digest as string)}
            className="font-mono text-slate-700 hover:text-bzm hover:underline">
            {props.copied === digestKey ? "Copied" : digestText(r)}
          </button>
        ) : (
          <span className={gapCls}
            title={unread ? r.registry_detail ?? undefined : undefined}>
            {digestText(r)}
          </span>
        )}
      </td>
      <td className="px-2 py-1.5">
        <code className="font-mono text-slate-700">{r.tag}</code>
        {resolved && (
          <span className={"block text-2xs "
            + (r.resolves_to ? "font-mono text-slate-700" : gapCls)}
            title={unread && !r.resolves_to ? r.registry_detail ?? undefined : undefined}>
            {resolved}
          </span>
        )}
        {tagNotes(r).map((t) => (
          <span key={t.kind} className={"block text-2xs " + TAG_TONE[t.kind]}>
            {t.text}
          </span>
        ))}
      </td>
      {props.showRequired && (
        <td className="px-3 py-1.5">
          {r.required === true && (
            <span className="text-3xs font-bold uppercase tracking-wide
                             bg-emerald-100 text-emerald-800 rounded px-1.5 py-0.5">
              required
            </span>
          )}
          {r.required === false && (
            <span className="text-2xs text-slate-400">not for this location</span>
          )}
        </td>
      )}
    </tr>
  );
}
