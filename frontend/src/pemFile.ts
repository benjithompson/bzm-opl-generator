/** A certificate file, read where it sits, as the PEM an option carries.
 *
 *  The customer configuring CA trust has a **file**, and this page asked them
 *  to open it in a text editor and paste it into a textarea (#227). The file
 *  picker beside that box is the same shape `--ca-bundle PEM_FILE` has had all
 *  along: the file is read where it exists and the option carries what was in
 *  it.
 *
 *  **`ca_bundle` stays content, never a path**, which is what makes this a
 *  browser-side read rather than a field holding a file name. A path-valued
 *  option cannot produce a bundle for a host nobody here can see, which is
 *  `facts.manual()`'s whole premise; the server never learns the file existed.
 *
 *  It refuses rather than filling the box with whatever the bytes decode to,
 *  and the two refusals are deliberately different sentences. A `.crt` is very
 *  often DER -- the same certificate in binary -- and `readAsText` on one gives
 *  replacement characters that go into a ConfigMap, mount cleanly and are
 *  trusted by nothing: an agent that starts, reports online and fails every
 *  handshake. That is a conversion, and the sentence says so. A file holding
 *  nothing is not that, and telling somebody to run `openssl` over an empty
 *  file is "could not read" wearing "there is nothing there".
 */

const BEGIN_CERT = "-----BEGIN CERTIFICATE-----";

export type Loaded =
  | { ok: true; pem: string; certs: number }
  | { ok: false; why: string };

/** How many certificate blocks the text carries.
 *
 *  A count of blocks, not a parse -- nothing here validates a certificate, and
 *  the number is reported so the customer can see whether the file is the whole
 *  bundle. That matters because the mount **replaces** the trust store rather
 *  than adding to it: a file holding their CA alone leaves the agent unable to
 *  trust BlazeMeter itself. */
export function certCount(pem: string): number {
  return pem.split(BEGIN_CERT).length - 1;
}

/** The chosen file's text as a bundle, or why it is not one. */
export function readCertFile(name: string, text: string): Loaded {
  // A file authored on Windows is the same bundle; the generator's own
  // splitlines() would cope, but the textarea this fills should not show ^M.
  const pem = text.replace(/\r\n/g, "\n").trim();
  if (!pem) {
    return { ok: false, why: `${name} is empty — it holds no certificate.` };
  }
  const certs = certCount(pem);
  if (!certs) {
    return {
      ok: false,
      why: `${name} carries no ${BEGIN_CERT} block. If it is DER (binary), `
        + `convert it first: openssl x509 -inform der -in ${name} -out ca.pem`,
    };
  }
  return { ok: true, pem, certs };
}
