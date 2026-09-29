# Images: what each one does, mirroring, and keeping a mirror current

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
it, else from a running agent's inventory, else from the built-in catalogue
(which floats on `latest`). Each row says which (`source`).

`--lookup` asks BlazeMeter's public registry, anonymously, for each image's
digest, its compressed size for linux/amd64, and the newest tag published in
the same series:

```
bzm-opl-gen images --facts facts.json --explain --lookup
```

A series is the tag's version with the same suffix: `2.4.533-reduced` compares
with `2.4.538-reduced`, and not with a branch build such as
`2.4.537-MOB-...-reduced`. A tag with no version (`latest`) has no series, so no
newest tag is reported for it.

Each registry read has its own state. `read` means the registry answered, and a
tag it does not hold is a read with no digest. `unread` means it did not answer
or refused, and the detail says how. The two never look the same. A failed
lookup never stops the command.

## Mirroring

Generate with `--private-registry <registry>` and the bundle carries
`bzm-opl-image-mirror.sh`, which pulls each image and pushes it to the name the
agent asks for. The names differ per platform: on Kubernetes the agent composes
`<registry>/<repo path>:<tag>`, and on a docker host `<registry>/blazemeter/<name>:latest`.
Keep the names the script uses. For a Kubernetes bundle,
`images --pull --mirror <registry>` does the same copy from this tool.

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
