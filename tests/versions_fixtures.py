"""`GET /private-locations/{h}/ships/{s}/versions`, recorded off real locations.

Three single-functionality locations in one real account, read verbatim and
trimmed of nothing. Two things about them are the whole point:

  - **No agent had ever been online.** The performance and GUI recordings come
    from agents in state `empty`, so this list needs no deployment, no live
    inventory and no heartbeat -- which is what closes the browser-image gap
    that `facts.gui_images_incomplete` was written for.
  - **The map's keys are BlazeMeter's resource ids, not crane's image keys.**
    `taurusEngineDockerImage` names nothing crane resolves an override by;
    `dockerTag` does, and `dockerTag:version` is exactly the form a live
    Kubernetes agent reports its images in. Read the id and a bundle's
    IMAGE_OVERRIDES gets a key crane never asks about.

One source file rather than a copy per suite, for the reason
`evidence_fixtures.py` is one: two recordings of the same endpoint drift, and
the halves that read them then disagree about what BlazeMeter answers.
"""

# funcIds ["performance"], agent state `empty`. Three resources -- and neither
# `torero` nor `richrach`, which the fallback catalogue lists for this
# functionality. See FALLBACK_IMAGES for what they turned out to be.
VERSIONS_PERFORMANCE = {
    "resources": {
        "apmDockerImage": {
            "dockerTag": "apm-image", "type": "dockerImage",
            "version": "1.7.112", "imageRelativePath": "blazemeter/apm",
            "restartPolicy": "Never", "minSlots": 1,
            "dockerRegistry": "gcr.io/verdant-bulwark-278"},
        "taurusEngineDockerImage": {
            "dockerTag": "taurus-cloud", "type": "dockerImage",
            "version": "2.4.454-reduced", "reducedVersion": "2.4.454-reduced",
            "imageRelativePath": "blazemeter/v4",
            "restartPolicy": "Never", "minSlots": 1,
            "dockerRegistry": "gcr.io/verdant-bulwark-278"},
        "blazemeter/crane": {
            "dockerTag": "blazemeter/crane", "type": "dockerImage",
            "version": "3.7.56", "windowsVersion": "3.6.127",
            "imageRelativePath": "blazemeter/crane",
            "restartPolicy": "Always", "minSlots": 1,
            "dockerRegistry": "gcr.io/verdant-bulwark-278"},
    }
}

# funcIds ["functionalGui", "firefox:default", "chrome:default",
# "MicrosoftEdge:default"], agent state `empty`. The taurus engine plus the grid
# proxy plus one pinned browser per browser funcId -- which is the version the
# account says this location runs, and the answer no catalogue could give.
VERSIONS_GUI = {
    "resources": {
        "apmDockerImage": VERSIONS_PERFORMANCE["resources"]["apmDockerImage"],
        "taurusEngineDockerImage":
            VERSIONS_PERFORMANCE["resources"]["taurusEngineDockerImage"],
        "blazemeter/doduo": {
            "dockerTag": "blazemeter/doduo", "type": "dockerImage",
            "version": "0.0.144", "imageRelativePath": "blazemeter/doduo",
            "restartPolicy": "Never", "minSlots": 2,
            "dockerRegistry": "gcr.io/verdant-bulwark-278"},
        "blazemeter/charmander/firefox/139": {
            "dockerTag": "blazemeter/charmander/firefox_139.0.4",
            "type": "dockerImage",
            "url": "https://storage.googleapis.com/blazemeter-images",
            "version": "2.10.45",
            "imageRelativePath": "blazemeter/charmander/firefox_139.0.4",
            "minSlots": 2, "dockerRegistry": "gcr.io/verdant-bulwark-278"},
        "blazemeter/charmander/chrome/136": {
            "dockerTag": "blazemeter/charmander/chrome_136.0.7103.113",
            "type": "dockerImage",
            "url": "https://storage.googleapis.com/blazemeter-images",
            "version": "2.10.45",
            "imageRelativePath": "blazemeter/charmander/chrome_136.0.7103.113",
            "minSlots": 2, "dockerRegistry": "gcr.io/verdant-bulwark-278"},
        "blazemeter/charmander/MicrosoftEdge/137": {
            "dockerTag": "blazemeter/charmander/microsoftedge_137.0.3296.83",
            "type": "dockerImage",
            "url": "https://storage.googleapis.com/blazemeter-images",
            "version": "2.10.45",
            "imageRelativePath":
                "blazemeter/charmander/microsoftedge_137.0.3296.83",
            "minSlots": 2, "dockerRegistry": "gcr.io/verdant-bulwark-278"},
        "blazemeter/crane": VERSIONS_PERFORMANCE["resources"]["blazemeter/crane"],
    }
}

# funcIds ["mockServices"]. Three resources, and **no taurus engine** -- an SV
# agent carries no `v4` and no `apm` at all, which is the evidence
# core.ENGINE_FUNCTIONALITIES and sv.exclusiveWith rest on.
VERSIONS_SV = {
    "resources": {
        "blazemeter/service-mock": {
            "dockerTag": "blazemeter/service-mock", "type": "dockerImage",
            "version": "6.0.30.4",
            "imageRelativePath": "blazemeter/service-mock",
            "restartPolicy": "Never", "minSlots": 1,
            "dockerRegistry": "gcr.io/verdant-bulwark-278"},
        "blazemeter/group-gateway": {
            "dockerTag": "blazemeter/group-gateway", "type": "dockerImage",
            "version": "6.0.30.4",
            "imageRelativePath": "blazemeter/group-gateway",
            "restartPolicy": "Never", "minSlots": 1,
            "dockerRegistry": "gcr.io/verdant-bulwark-278"},
        "blazemeter/crane": VERSIONS_PERFORMANCE["resources"]["blazemeter/crane"],
    }
}
