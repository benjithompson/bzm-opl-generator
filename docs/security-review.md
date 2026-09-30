# The security review

Every bundle, in every format, carries `SECURITY-REVIEW.md`. It is one document
for the people who approve a deployment: a change-approval board, a security
team or a platform team. It says what the agent runs, which hosts it connects
to, what it is allowed to do and what it holds.

The document is written from the same options as the files beside it. When you
generate the bundle again, the document changes with it.

## Print it without writing a bundle

```
bzm-opl-gen review --facts facts.json --profile out/profile.json
bzm-opl-gen review --facts facts.json --profile my-options.json -o review.md
```

`--profile` takes any options file, such as the `profile.json` that a bundle
writes. Without `--profile`, every option has its default. The command writes
no bundle and does not read or issue an AUTH_TOKEN.

The MCP server gives the same document with `opl_bundle review {facts,
options?}` ([mcp.md](mcp.md)).

## What it contains

| Section | Content | Where the content comes from |
|---|---|---|
| What runs | The crane Deployment, the ServiceAccount, the pods crane starts for each functionality, the crane-hook check. For manifests, a table of every object the bundle applies. | The rendered objects and the location's functionalities |
| Images | Each image, what it does, which functionality needs it, when it is pulled, and whether its tag floats | The image catalogue that `IMAGES.md` also uses ([images.md](images.md)) |
| Network | The hosts and ports the agent and the engines connect to, the registry, the proxy in force (credentials removed), `NO_PROXY`, and what is published: Service type and virtual-service ingress | The bundle's options and BlazeMeter's fixed hosts |
| TLS trust | The CA mode in force, where the CA is mounted, and the `ca-check` command for this bundle | The CA options ([ca-trust.md](ca-trust.md)) |
| Kubernetes permissions | Every rule of every Role and ClusterRole, with its scope, including the crane-hook Role | **Parsed from the rendered Role and ClusterRole objects** |
| Pod security | Crane's security contexts, whether they meet the `restricted` Pod Security Standard, and the engine posture | **Parsed from the rendered Deployment and ConfigMap** ([hardened-engines.md](hardened-engines.md)) |
| Resources | Requests and limits for crane and each engine, the total at full concurrency, and the node size | The engine size and the location's `slots` |
| Secrets | What each Secret or env file holds (key names only), that crane writes the AUTH_TOKEN to its own log at startup and how to restrict that log, and what `profile.json` leaves out | **Parsed from the rendered Secret**; the log line was seen with crane 3.8.0 |

A docker bundle has **Host access** in place of the Kubernetes permissions and
pod security sections: the docker socket, and why the container runs as root.
It does not describe an option that the docker format ignores.

## What it never contains

- The AUTH_TOKEN, a proxy password or a private key. The document names the
  file or the Secret that holds each one.
- A marker. A field left blank shows as a lower-case sample, such as
  `<namespace>`, and the bundle's `README.md` lists the fields to fill in.

## What it does not cover

- The systems your tests target. Engines connect to them, and those hosts come
  from your test scripts.
- A Helm release name other than `crane`. The chart applies the same objects as
  the manifests; `helm template` prints their names for your release.
- What a cluster's own admission control, quota or network policy does. Use
  `bzm-opl-gen doctor` for that ([preflight.md](preflight.md)).
