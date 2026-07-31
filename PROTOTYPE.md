# Configure-step UX prototype — the answer

Throwaway. This file and everything under `frontend/src/prototype/` live on
`prototype/configure-layout` and are **not** meant for `main`. What `main` should
get is the decision below, rewritten properly.

## The question

The configure step's feature buttons switched a *view*. Five of the six option
groups belong to no feature, so they appeared in both views: pressing a button
changed one row while reading as though it changed the whole step, and the two
views were mostly the same page twice.

Thirteen variants were built on the real page (`?variant=A…M`, floating switcher,
dev builds only). Each is one commit; `git log main..prototype/configure-layout`
reads in order.

## The answer: **I + G + M**, with the refinements below

| layer | variant | what it is |
|---|---|---|
| configure body | **A + B's rail** (`D`) | shared "Deployment settings" deck + a card per feature, with a rail naming what is set |
| preview | **G** | `Configure` / `Preview (n)` tabs; the form gets the full width |
| step flow | **I** | one step at a time, stepper across the top, Back/Next in that same bar, panel scrolls inside itself so the page never scrolls |
| step 1 | **M** | a path line (`LOCATION x › AGENT y`), agent list styled like the location list, rows that expand |

Rejected, and why: **B** (rail + one long form) re-introduces the vertical stack;
**C** (one nested list) is the most extensible but buries the shared settings;
**D/E/F** put the preview in a drawer / inline / slide-over — F was the runner-up
and is the one to revisit if watching the YAML while typing turns out to matter;
**H/J** move the wizard controls to a footer / the screen edges, which are further
from where the eye lands after a field; **K/L** annotate the location list or split
it master-detail without M's path line.

## Decisions taken along the way

- The feature selector is gone. Nothing appears or disappears when a feature is
  touched, so `visibleGroups` / `hiddenGroups` / `setButHidden` / `hiddenBlockers`
  in `optionGroups.ts` lose their reason to exist — they existed only to patch
  view-switching.
- A step that is complete only because everything has a default, and was never
  opened, reads **"ready — nothing required"**, not a tick.
- The source block must **not** collapse in a step flow: picking a location
  auto-selects its lone offline agent, which used to swap the panel for a summary
  and take the agent list with it.
- Reusing an agent identity needs a credential, and none can be read back — so
  regeneration is an explicit two-press red button (`Regenerate token` →
  `I'm sure` + `Cancel` → `Regenerated`) that fills the AUTH_TOKEN field and turns
  the rotate flag **off**: core's rule is that a token in the form wins, and a
  ticked box would promise a rotation that never happens.
- Feature state is two words: `Enabled` / `Not enabled`, at the top of the card.
- A feature the location does not run answers a click instead of ignoring it:
  *Enable it and configure it here?* with `Enable on location` / `Cancel`.
- Anything that waits on the network says so (`locBusy`, `factsBusy`, `Creating…`,
  `Regenerating…`).

## Stubs — real work before any of this ships

1. **Token regeneration has no endpoint.** Rotation only happens inside
   generate/download (`rotate_token`); there is no route that issues one on its
   own. The button writes `PROTOTYPE_STUB_NOT_A_REAL_TOKEN_<shipId>`. Needs one
   route over `core.rotate_auth_token`, and the UI wiring is then the same three
   states with the response swapped in.
2. **"Enable on location" writes nothing.** Changing a location's funcIds is a
   PATCH this build does not make; the card says the account still disagrees.
3. **crane-hook generates nothing.** The switch writes `crane_hook`.
   [crane-hook](https://github.com/Blazemeter/crane-hook) is a one-shot Pod plus
   its own Role and RoleBinding (`restartPolicy: Never`) that checks node
   capacity, egress, RBAC and — for SV — ingress and its TLS secret, exiting 0 or
   1; helm-crane 1.4.0+ packages the same image as a `helm test` hook. Every
   variable it takes is a value the bundle already decides. Shipping it needs a
   template in `generate.py`, the Go-template half for helm parity, a row in
   `bzm_opl_gen/options.py`, and offline + parity tests.

## Shipped-code changes that came with it

These are in `App.tsx` and friends rather than `prototype/`, and are worth keeping
whichever layout wins:

- `createShipNow`, the account/workspace selects and the create-location form
  lifted out of the JSX so more than one panel can use them (moved, not copied).
- `locBusy` / `factsBusy` around the two fetches.
- `helm upgrade` no longer named as what agent auto-update breaks — crane's
  rewrite conflicts with *any* later apply.
- Concision pass over the hints and warnings, and `AUTH_TOKEN as entered above`
  → `the generated AUTH_TOKEN` (`token.test.ts` matcher updated with it).
- Watch agent status rebuilt as an option-group row that names its agent.

## Folding it in

Rewrite under prototype constraints is not production code. The order that keeps
each step testable:

1. Split `App.tsx`'s three `<Section>` bodies into components (they are already
   addressable — `StepFlow` reads them as children today).
2. Land the step flow (I) and the tabs (G) as the real layout; delete
   `PROTO_*` and `src/prototype/`.
3. Land the configure split (A + rail); delete the view machinery from
   `optionGroups.ts` and its tests, and add tests for the new split.
4. Land step 1 (M) — path line, agent accordion, the credential in the row.
5. Only then the three stubs above, each with its own endpoint and tests.
