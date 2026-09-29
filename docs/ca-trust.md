# CA trust

A network that inspects TLS re-signs every connection with the
organisation's own certificate authority (CA). Crane and the engines it starts
must trust that CA, or every call to BlazeMeter fails with
`CERTIFICATE_VERIFY_FAILED` and the agent never comes online.

The bundle you give the generator **replaces** the agent's own trust store
(`REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE`). It must therefore hold your CA *and*
the public roots, unless every connection the agent makes goes through the
inspecting proxy. [Options: CA trust](options.md#ca-trust) lists the four ways
to supply it: an inline PEM, a certificate file supplied later, an existing
ConfigMap, or OpenShift injection.

## Check your CA before deploying

Run `ca-check` on a machine on the **same network as the agent**, through the
**same proxy** the agent will use:

```
bzm-opl-gen ca-check --ca-bundle corp-ca.pem --proxy http://proxy.corp:3128
```

It does two things:

1. It reads the file and lints every certificate in it (below).
2. It opens TLS to each BlazeMeter host, prints the certificate chain that the
   network presents, and verifies that chain against **your bundle alone** —
   not against the machine's own trust store.

The default hosts are the API crane registers with (`a.blazemeter.com`) and the
hosts engines upload results to (`data.blazemeter.com`,
`storage.blazemeter.com`). `--host HOST[:PORT]` replaces them and repeats.
`--registry reg.corp:5001/blazemeter` adds a private registry. `--json` prints
the result as data.

Without `--proxy`, the check reads `HTTPS_PROXY` and `NO_PROXY` as curl does.
Put proxy credentials in the URL (`http://user:pass@proxy.corp:3128`); they are
sent in a `Proxy-Authorization` header and never printed.

### What each host reports

| Result | Meaning | What to do |
|---|---|---|
| `VERIFIED` | The bundle verifies the chain this network presents. | Nothing. |
| `NOT VERIFIED` | The bundle does not verify the chain. The `missing:` line names the CA the bundle lacks. | Ask your security team for that CA certificate, and its issuers up to the root, and add them to the bundle. |
| `UNREACHABLE` | No TLS connection was made: DNS, a firewall, a timeout, or a proxy that refused `CONNECT` (for example `407` for missing credentials). | Nothing was judged. Run the check from a machine that can reach the host. |

Verification follows OpenSSL's default, which is also curl's: the chain must end
at a **self-signed root in the bundle**. An intermediate CA alone does not
verify, even though some clients accept it.

The command exits `1` when a host is not verified or the lint has a `FAIL`. An
unreachable host does not change the exit status, because it was not judged.

### What the lint reports

| Severity | Finding | Fix |
|---|---|---|
| FAIL | A certificate is not a CA: a server (leaf) certificate, often exported from a browser padlock. | Replace it with the root CA named in its issuer. |
| FAIL | A certificate has expired. | Ask for its replacement. |
| FAIL | The file holds no certificate at all. | Supply the PEM, DER or PKCS#7 file itself. |
| WARN | A certificate expires in fewer than 30 days. | Ask for its replacement now. |
| WARN | An intermediate CA's issuer is not in the bundle. | Add the root. It is fine only where the client also reads a system store that holds the root, and crane reads this bundle in place of its own store. |
| WARN | A block does not parse, has no `END` line, or is not a certificate (a private key, a CRL). | Remove or replace it. A private key never belongs in a trust bundle. |
| NOTE | A certificate appears twice. | Nothing; the copy can be removed. |

A file that cannot be opened is an error of its own, not "no certificate".

### Windows exports and line ends

`ca-check` and `generate --ca-bundle` read a PEM file, a DER `.cer`, or a
PKCS#7 `.p7b` (DER or PEM), with Windows (CRLF) or Unix line ends. `generate`
writes all of them into the bundle as PEM. To convert by hand for another tool:

```
openssl x509 -inform der -in corp-root.cer -out corp-root.pem
openssl pkcs7 -inform der -print_certs -in corp-chain.p7b -out corp-chain.pem
```

A full bundle is your CA followed by the public roots, for example on Debian or
Ubuntu:

```
cat corp-root.pem /etc/ssl/certs/ca-certificates.crt > ca-bundle.crt
```

### A private registry

The node's container runtime pulls images with the **node's** trust store, not
with this bundle. A `--registry` result says whether your CA covers the
registry. The nodes need that CA in their own store as well.

## At generate time

With an inline PEM (`--ca-bundle`, `ca_bundle`), `generate` lints the bundle and
prints each finding: on stderr from the command line, in `warnings` from the
MCP server and in the web preview's response. A `FAIL` is warned loudly and the
bundle is still written; nothing that was accepted before is refused. The other
modes carry no PEM at generate time, so check their file with `ca-check`.

## The doctor's egress probe

`doctor` probes egress from the crane pod when crane is deployed. Before that,
it starts one throwaway curl pod and hands it the CA the bundle configures, so
a corporate network is probed rather than skipped:

| CA mode | Where the probe's CA comes from | Not probed (WARN) when |
|---|---|---|
| inline | the PEM in `profile.json` | the PEM holds no certificate |
| existing ConfigMap | that ConfigMap, read from the namespace | it is not there, cannot be read, or has no such key |
| certificate file | the ConfigMap built from the file | it is not created yet, or the file is not named yet |
| OpenShift injection | the injected ConfigMap | it is not applied yet, or OpenShift has not filled it |

"Not there" and "could not be read" are different reasons in the report. A probe
that fails verification (curl exit `60`) is a FAIL that points to `ca-check`.
The CA reaches the pod on stdin, so a large bundle is not limited by the
command-line length.
