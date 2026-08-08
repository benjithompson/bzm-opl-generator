import { useState } from "react";
import { ErrorMsg, Field, TextInput } from "../components";
import { Applies } from "../formats";
import { CaMode } from "../optionGroups";
import { certCount, readCertFile } from "../pemFile";

// Who owns the bundle, per mode -- the difference that decides which one to
// pick, and the reason the modes are radios rather than three sets of fields.
// `key` is the option the mode writes, so a format that carries no ConfigMap
// drops the two ConfigMap modes without this file knowing which format that is.
const CA_MODES: { mode: CaMode; label: string; hint: string; key: string }[] = [
  {
    // First, and the default (optionGroups.enable), because it is the moment
    // most customers are in when they discover they need this at all: crane is
    // failing TLS and the certificate is still with the platform team (#230).
    mode: "slot",
    label: "Create the ConfigMap here — certificate to follow",
    hint: "the bundle carries it wired to the agent, with the PEM slot marked; paste the certificate in when it arrives, then deploy",
    key: "ca_bundle_slot",
  },
  {
    mode: "inline",
    label: "Create the ConfigMap here — I have the certificate",
    hint: "the PEM goes into the bundle; rotation means regenerating and re-applying",
    key: "ca_bundle",
  },
  {
    mode: "existing",
    label: "Reference a ConfigMap your platform team owns",
    hint: "they own and rotate the trust bundle (e.g. via trust-manager) and this bundle only points at it — so the ConfigMap has to exist already",
    key: "ca_existing_configmap",
  },
  {
    mode: "inject",
    label: "OpenShift cluster trust injection",
    hint: "empty ConfigMap labeled inject-trusted-cabundle; the cluster injects and rotates ca-bundle.crt — OpenShift only",
    key: "ca_openshift_inject",
  },
];

/** Custom CA trust. The mode is derived from the options rather than stored
 *  (caModeOf), so the radios and the group's own switch cannot disagree.
 *
 *  With one mode left there is nothing to choose, so the radios go and the PEM
 *  field is the group -- a single radio that cannot be unpicked is a control
 *  pretending to be a question. That is the docker case: the bundle writes the
 *  PEM beside its script and mounts the file, and the other two modes name a
 *  ConfigMap there is nothing to read one out of. `generate._ca_cfg` agrees --
 *  it stops counting those two as competing modes for that format, so a bundle
 *  configured for Kubernetes and switched here is not refused over the
 *  ConfigMap name it still carries. */
export function CaGroup(props: {
  applies: Applies;
  /** Is the target cluster OpenShift? The injection mode is the cluster's own
   *  operator filling a labeled ConfigMap, so off OpenShift it is a mode that
   *  emits an empty ConfigMap and trusts nothing extra -- a silent failure, and
   *  the one thing this group's radios must not offer. It is the *cluster* and
   *  not the SCC posture: the posture is the recommended one on vanilla
   *  Kubernetes too, and reading the mode off it was how this got offered there.
   *  Turning the cluster toggle off clears the option as well as hiding the
   *  radio (see AdvancedRow), so the mode cannot survive off screen. */
  openshift: boolean;
  mode: CaMode;
  onMode: (m: CaMode) => void;
  configmap: string;
  configmapKey: string;
  bundle: string;
  onConfigmap: (v: string) => void;
  onConfigmapKey: (v: string | null) => void;
  onBundle: (v: string) => void;
}) {
  const modes = CA_MODES.filter(
    (m) => props.applies(m.key) && (props.openshift || m.mode !== "inject"));
  // Nothing to choose: the mode is the only one this format has, whatever the
  // options say. `caModeOf` can only have read one the format does not carry
  // (Kubernetes, then switched), and that value is kept -- the generator names
  // it in the README rather than dropping it -- but what is on screen is the
  // field that reaches something.
  const single = modes.length === 1;
  const mode = single ? modes[0].mode : props.mode;
  // What the last pick did. Local to this view, like every other busy flag on
  // the page: nothing here reaches the server, and the option it fills is
  // App's. Three outcomes and they are three sentences -- the file was read,
  // the file could not be read at all, and the file was read and holds no
  // certificate. Collapsing the last two would tell somebody to convert a file
  // nothing ever opened.
  //
  // `saw` is the bundle the note is true *about*, and it is what stops the note
  // outliving it. Switching the mode away and back clears `ca_bundle`, and
  // pasting over a refused file corrects the box; either way the sentence on
  // screen would go on describing a value that is no longer there -- a green
  // "2 certificates" above an empty box, or an `openssl` refusal under a bundle
  // that is now fine. Compared at render rather than cleared by an effect: the
  // question is whether the note still describes what is in the box, and that
  // is answerable from the props.
  const [note, setNote] =
    useState<{ ok: boolean; msg: string; saw: string } | null>(null);
  const pick = (f: File) => {
    const held = props.bundle;
    f.text().then((text) => {
      const read = readCertFile(f.name, text);
      // A refusal writes nothing, so the note it leaves is about the value
      // already in the box.
      if (!read.ok) { setNote({ ok: false, msg: read.why, saw: held }); return; }
      props.onBundle(read.pem);
      setNote({ ok: true, saw: read.pem,
                msg: `${f.name} — ${read.certs} certificate`
                     + (read.certs === 1 ? "" : "s") });
    }).catch(() => setNote(
      { ok: false, msg: `${f.name} could not be read.`, saw: held }));
  };
  const current = note && note.saw === props.bundle ? note : null;
  return (
    <>
      {!single && (
        <div className="space-y-1.5 text-sm">
          {modes.map((m) => (
            <label key={m.mode} className="flex items-start gap-2 cursor-pointer select-none">
              <input type="radio" name="ca-mode" className="mt-1 accent-bzm"
                checked={mode === m.mode} onChange={() => props.onMode(m.mode)} />
              <span>{m.label}
                <span className="block text-[11px] text-slate-400">{m.hint}</span>
              </span>
            </label>
          ))}
        </div>
      )}
      {mode === "existing" && (
        <div className="grid grid-cols-2 gap-2">
          <Field label="ConfigMap name">
            <TextInput mono placeholder="corp-trust-bundle"
              value={props.configmap}
              onChange={props.onConfigmap} />
          </Field>
          <Field label="Bundle key" hint="file key inside the ConfigMap">
            <TextInput mono placeholder="ca-bundle.crt"
              value={props.configmapKey}
              onChange={(v) => props.onConfigmapKey(v || null)} />
          </Field>
        </div>
      )}
      {mode === "slot" && (
        <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2
                      text-[11px] text-slate-600">
          <code>bzm_cacerts.yaml</code> ships in the bundle, wired to the agent,
          with <code>&lt;CA_BUNDLE&gt;</code> where the PEM goes. Paste the
          certificate into it when your team sends it, then deploy as normal.
          Nothing else needs changing.
        </p>
      )}
      {mode === "inline" && (
        <>
          {/* The paste box is gone. A customer configuring CA trust has a
              *file*, and a textarea asked them to open it in an editor first --
              which is what made this mode feel wrong for the thing they were
              holding (#227). What is left states what was read, because the
              value is no longer visible: a PEM is not something anybody proofs
              by eye in a 3-row box, and the certificate count is the fact worth
              reading back. */}
          <Field label="CA bundle (PEM)">
            <p className="mt-0.5 text-sm text-slate-600">
              {props.bundle.trim()
                ? `${certCount(props.bundle)} certificate`
                  + (certCount(props.bundle) === 1 ? "" : "s") + " loaded"
                : "No certificate chosen yet."}
            </p>
          </Field>
          <div className="flex items-center gap-2 flex-wrap">
            <label className="rounded-md px-3 py-1.5 text-sm font-medium border
                              border-slate-300 text-slate-600 hover:bg-slate-50
                              cursor-pointer">
              {props.bundle.trim() ? "Replace file" : "Choose file"}
              {/* Wide on purpose, `.der` included. A file this cannot use is
                  better picked and refused than greyed out: the refusal names
                  `openssl x509`, and a dialog that hides the customer's file
                  says nothing at all. */}
              <input type="file"
                accept=".pem,.crt,.cer,.cert,.der,.ca-bundle,.txt"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  // Cleared so picking the same file twice fires again -- the
                  // second pick is what somebody does after converting it.
                  e.target.value = "";
                  if (f) pick(f);
                }} />
            </label>
            {props.bundle.trim() && (
              <button type="button"
                className="rounded-md px-3 py-1.5 text-sm font-medium border
                           border-slate-300 text-slate-600 hover:bg-slate-50"
                onClick={() => props.onBundle("")}>Remove</button>
            )}
            <span className="text-[11px] text-slate-400">
              read in your browser; the bundle carries the certificate itself,
              never a path
            </span>
          </div>
          {current?.ok && (
            <p className="text-[11px] text-slate-500">{current.msg}</p>
          )}
          <ErrorMsg msg={current && !current.ok ? current.msg : null} />
        </>
      )}
      {/* Where it ends up, which is the whole difference between the two
          platforms: a ConfigMap the pods mount, or a file beside the script the
          container mounts. Both end with the same two variables pointed at it,
          because crane's HTTP client reads one and boto the other. */}
      <p className="text-[11px] text-slate-400">
        {!single
          ? <>Mounted read-only at /var/cm in crane; engines get the same
              ConfigMap via KUBERNETES_CA_BUNDLE_MOUNT, and
              REQUESTS_CA_BUNDLE / AWS_CA_BUNDLE point at it.</>
          : <>Written beside the run script and mounted into the container;
              REQUESTS_CA_BUNDLE / AWS_CA_BUNDLE point at it.</>}
      </p>
    </>
  );
}
