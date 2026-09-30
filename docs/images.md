# Images: what each one does, mirroring, air-gapped sites, and keeping a mirror current

A private location pulls BlazeMeter's container images from
`gcr.io/verdant-bulwark-278`. A cluster that cannot reach that registry needs
every image the location uses copied into a registry it can reach. This page
tells you which images those are, what each one does, and how to check a mirror.

## Which images, and why

`bzm-opl-gen images --explain` prints one row per image the location pulls:

```
bzm-opl-gen images --api-key api-key.json --harbor-id <harbor-id> --explain
bzm-opl-gen images --explain                       # the whole built-in catalogue
bzm-opl-gen images --facts facts.json --explain --format md
```

Each row gives:

| field | meaning |
|---|---|
| functionality | the funcIds whose agent runs this image (`performance`, `functionalGui`, `mockServices`, ...). The agent image serves every functionality. |
| purpose | what the image does, in one sentence |
| when pulled | the event that makes the agent pull it: a deploy, every test run, a virtual service deploy |
| seen in a live run | `yes` when the image was observed doing that job on a real location. `no` means the purpose or the moment is inferred from the image name, the location's image list or a direct probe of the image. |
| required | the location's funcIds select it. `--all` adds the images they do not select, as not required. |
| floating tag | the tag has no version (`latest`), so the image it names changes when BlazeMeter publishes |

`--format` is `table` (the default), `md`, `csv` or `json`.

What is known about each image:

| image | functionality | purpose | seen in a live run |
|---|---|---|---|
| `blazemeter/crane` | every | the agent itself | yes |
| `blazemeter/v4` (key `taurus-cloud`) | performance, API and GUI functional | the test engine | yes |
| `blazemeter/apm` | performance, API and GUI functional | APM support for engines (inferred from the name) | no |
| `blazemeter/torero`, `blazemeter/richrach` | performance, API and GUI functional | undocumented; a Kubernetes agent pulls them ahead of use, and no observed run starts them ([hardened-engines.md](hardened-engines.md#torero-and-richrach-started-but-not-exercised)) | no |
| `blazemeter/doduo` | GUI functional | the Selenium grid proxy | yes |
| `blazemeter/charmander/<browser>` | GUI functional | one pinned browser build; the location's image list names which | Chrome: yes |
| `blazemeter/service-mock` | service virtualization | serves one virtual service | yes |
| `blazemeter/group-gateway` | service virtualization | the gateway in front of the virtual services (inferred) | no |
| `blazemeter/mock-pc-service` | service virtualization | inferred from the name only | no |
| `blazemeter/proxy-recorder` | proxy recorder | records traffic into a test script | no |
| `cranehook` | none | the optional `crane_hook` preflight check | no |

## Versions, digests and sizes

The versions come from the location's own image list when `facts` could read
it, else from a running agent's inventory, else from the built-in catalogue.
Each row says which (`source`: `location-versions`, `agent-inventory`,
`registry-newest` or `catalogue`).

### Without an account

`facts --manual`, the web UI's manual entry, and the whole-catalogue view
cannot read the location's list. They pin each image to the **newest release
in BlazeMeter's registry** instead (`source: registry-newest`), crane
included, so a bundle deploys a current agent.

- `latest` is not used, because on BlazeMeter's registry it names releases far
  older than the newest. For the test engine it was an earlier major version
  when this was checked.
- A release is the repository's own release shape: `X.Y.Z` for crane, apm,
  doduo and the proxy recorder, `X.Y.Z-reduced` for the engine, and `X.Y.Z.N`
  with a small `N` for the virtual-service images. Branch builds such as
  `-MOB-...` never match.
- torero and richrach keep `latest`: the agent asks for them by that tag.
- If the registry does not answer within a few seconds, the images keep the
  catalogue's tags (`source: catalogue`) and the facts carry a warning.
- A location can ask for an older release than the newest (one was seen
  listing engine `2.4.533-reduced` while `2.4.538-reduced` was the newest).
  A mirror built without an account can then lack the image the agent asks
  for. Connect an API key for the exact list, put a pull-through cache in
  front of BlazeMeter's registry, or run `images --verify` once the agent is
  online.

The whole-catalogue view pins only when it asks the registry anyway
(`--lookup`, or `lookup=true` on `/api/images`).

`--lookup` asks BlazeMeter's public registry, anonymously, for each image's
digest, its compressed size for linux/amd64, and the newest tag published in
the same series:

```
bzm-opl-gen images --facts facts.json --explain --lookup
```

A series is the tag's version with the same suffix: `2.4.533-reduced` compares
with `2.4.538-reduced`, and not with a branch build such as
`2.4.537-MOB-...-reduced`. For an image with a release shape (see
[Without an account](#without-an-account)), the newest tag is the newest
release in that shape, the same tag a bundle made without an account pins. A
CI build such as `6.0.35.2347` is never offered as an update.

A floating tag (`latest`) has no version of its own. `--lookup` names the
version it is now (`resolves_to`): it compares the tag's digest with the
digests of the 10 newest versioned tags, newest first, and stops at the first
match. The newest tag is then the newest in that version's series. When no
tag matches, or a comparison could not be read, `resolves_to` is empty and the
registry detail says which.

Each registry read has its own state. `read` means the registry answered, and a
tag it does not hold is a read with no digest. `unread` means it did not answer
or refused, and the detail says how. The two never look the same. A failed
lookup never stops the command.

## Mirroring

Generate with `--private-registry <registry>` and the bundle carries
`bzm-opl-image-mirror.sh`, which pulls each image and pushes it to the name the
agent asks for. The names differ per platform: on Kubernetes the agent composes
`<registry>/<repo path>:<tag>`, and on a docker host `<registry>/blazemeter/<name>:latest`.
Keep the names the script uses. `images --pull --mirror <registry>` does the
same copy from this tool, to the same names; pass the bundle's
`--profile profile.json` for a docker bundle's names.

## Air-gapped sites

The mirror script and `--pull --mirror` need one host that reaches both
BlazeMeter's registry and yours. An air-gapped site has no such host. For that
site, save the images to files on a connected machine, carry the files
across, and load them into your registry on the far side.

1. On a connected machine, save the images:

   ```
   bzm-opl-gen images --api-key api-key.json --harbor-id <harbor-id> \
       --save /media/bzm-images --profile out/profile.json
   ```

   The command writes one archive file per image, `images-manifest.json` and
   `SHA256SUMS` into the directory. It saves the images the mirror script
   copies: the agent, the images the location's funcIds select (every image
   with `--all`), and the `crane_hook` image when the profile enables it.
   `--facts facts.json` works in place of the API key.
2. Carry the whole directory to the air-gapped side. Check it there with
   `sha256sum -c SHA256SUMS` if you want to; the load checks it again.
3. On the air-gapped side, load the images into your registry:

   ```
   bzm-opl-gen images --load /media/bzm-images --mirror registry.example.com/blazemeter
   ```

   The load needs no API key and no network access to BlazeMeter. It pushes
   each image to the name the agent asks for, the same name the mirror script
   uses. Then it runs the `--verify` check and reports each image as
   `present`, `missing` or `unread`. It exits 1 when an image is missing.

Add `--dry-run` to either command to print the commands and run none.

### The tools

| tool | used when | the archive |
|---|---|---|
| `skopeo` | on `PATH` (preferred) | `oci-archive`: the compressed layers, copied from BlazeMeter's registry with no docker daemon |
| `docker` | skopeo is not on `PATH`, or `--tool docker` | `docker save` output: the layers uncompressed, so the files are larger |

- Both copy the `linux/amd64` image. BlazeMeter publishes no other.
- skopeo copies each image by the digest it read from the registry just before,
  so the file holds exactly the image the manifest names.
- An `oci-archive` file loads only with skopeo. A `docker save` file loads
  with skopeo or with `docker load`, `docker tag` and `docker push`.
- A `docker save` file holds the layers uncompressed, so the push compresses
  them again. The image in your registry then has a digest different from
  the digest in BlazeMeter's registry and in `images-manifest.json`. This was
  measured: `crane:3.7.55` pushed from a `docker save` file got the digest
  `sha256:3004be93…`, not the digest BlazeMeter's registry gives. The agent
  pulls by tag, so a different digest does not stop it. Compare the tag, not
  the digest, when you check a mirror.
- The command prints each command before it runs it, prefixed `+ `.
- skopeo runs with `TMPDIR` set to the save directory, because it unpacks each
  image there for a moment. A small `/var/tmp` then does not stop it.

### What the save writes

`images-manifest.json` records, for each image:

| field | meaning |
|---|---|
| `ref` | the image in BlazeMeter's registry |
| `digest` | the digest the registry answered with at save time; empty when the registry could not be read |
| `size_mb` | the compressed linux/amd64 size the registry answered with; empty when it could not be read |
| `source` | where the version came from: `location-versions`, `agent-inventory`, `registry-newest` or `catalogue` |
| `archive`, `bytes`, `sha256` | the file, its length and its SHA-256, measured after the save |
| `target_path` | the name below your registry prefix that the agent asks for, for the profile the save used |

It also records the facts and the profile the save used, without the
AUTH_TOKEN or the virtual-service key, so the load needs neither the account nor
the bundle. The load uses the recorded profile unless you give `--profile`.

The save writes `images-manifest.json` last. A save that stops part of the way
leaves no manifest, and the load refuses that directory. Run the save again.

### Checks

| check | when | result |
|---|---|---|
| free space | before the save | The need is the sum of the compressed sizes the registry answered with. skopeo also needs space for the largest image a second time. Less free space than that stops the save. When a size could not be read, or the tool is docker (its files are uncompressed, by an amount nobody publishes), the command warns and continues. It never states a need it did not read. |
| checksums | before the first push | Each archive must be present, have the length and SHA-256 the save recorded, and agree with `SHA256SUMS`. One damaged file stops the load before anything is pushed, and the message names the file. |
| free space | before a skopeo load | skopeo unpacks each archive in the save directory. Less free space than the largest archive stops the load. |
| the registry | after the load | the `--verify` check below |

### Credentials and certificates

- Pulling from BlazeMeter's registry needs no credentials.
- skopeo pushes with `BZM_REGISTRY_USER` and `BZM_REGISTRY_PASSWORD` when you
  set them. The command writes them to a temporary auth file that only you can
  read, and deletes it after. The password is never on a command line. Without
  the two variables, skopeo uses its own login (`skopeo login`) or your docker
  config.
- docker pushes with the `docker login` already in place.
- `--ca-file <pem>` reaches skopeo as a certificate directory. docker reads a
  registry CA only from `/etc/docker/certs.d/<host>/ca.crt`, so for docker
  `--ca-file` applies to the check after the push only.
- `--mirror` takes a scheme. `--mirror http://registry.local:5000/bzm` is a
  registry that serves plain HTTP. The pushed names never carry the scheme,
  and the check after the push uses the same scheme as the push. Without a
  scheme, the rule is docker's own: `localhost` and `127.0.0.0/8` are plain
  HTTP, and every other host is HTTPS. Write `https://localhost:…` for a
  local registry that serves TLS.
- docker pushes to a plain-HTTP registry other than `localhost` and
  `127.0.0.0/8` only when the registry is in the daemon's
  `insecure-registries`. The load says so when this applies.

The MCP server's `opl_bundle images` plans a save or a load
(`transfer: "save"` or `"load"`) and returns the commands. It never runs them.

## Checking a mirror

```
bzm-opl-gen images --api-key api-key.json --harbor-id <harbor-id> \
    --verify registry.example.com/blazemeter --profile out/profile.json
```

`--verify` asks the registry for each image under the name the mirror script
pushes it to, and reports it as `present`, `missing` or `unread`:

| result | meaning | exit code |
|---|---|---|
| present | the registry holds the tag; its digest is printed | 0 |
| missing | the registry answered that it has no such tag | 1 |
| unread | the registry refused (401, 403) or did not answer; it may or may not hold the tag | 0, with a warning |

- `--profile` is the bundle's `profile.json`. Its format and `crane_hook`
  decide the names. Without it the names are a Kubernetes bundle's.
- Credentials come from `BZM_REGISTRY_USER` and `BZM_REGISTRY_PASSWORD`, else
  from an inline `auth` entry in `~/.docker/config.json`. A docker credential
  helper is not called; set the two variables instead. No credential is ever a
  command-line value.
- `--ca-file <pem>` trusts a registry whose certificate your own CA signed.
- Prefix the registry with `http://` for a registry that serves plain HTTP.
  Without a prefix, `localhost` and `127.0.0.0/8` are plain HTTP, as docker
  treats them, and every other host is HTTPS. Prefix `https://` for a local
  registry that serves TLS.

## Keeping a mirror current

Versioned tags follow BlazeMeter's releases. When BlazeMeter publishes a new
engine, the location's image list moves to the new tag, and an agent that pulls
from a mirror fails on its next test until the mirror has it. A floating tag
(`latest`) goes stale without any error: the mirror keeps the image it copied.

After a BlazeMeter release, or after you re-generate a bundle, run `--verify`
again and re-run the mirror script for anything missing.

## Every bundle carries IMAGES.md

Each generated bundle, in every format, has an `IMAGES.md` for whoever deploys
it: the images this agent pulls, what each does, which functionality needs it,
the mirror destinations when a private registry is set, and the `--verify`
command. It is written without network access, so it carries no digest or size.

## The same answer elsewhere

- The web UI server answers `GET /api/images?harbor_id=<id>&lookup=true&all=false`.
  Without `harbor_id` it answers for the whole catalogue and needs no API key.
- The MCP server's `opl_bundle images` returns the rows as `catalogue`, with
  `lookup=true` for the registry fields.
