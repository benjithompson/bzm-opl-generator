"""BlazeMeter's published footprint and fixed hosts, shared by the generator,
doctor and the planner.

A leaf module: plan.py imports it and must reach nothing.
"""


# The BlazeMeter API the agent registers against.
API_BASE = "https://a.blazemeter.com/api/v4"

# Hosts only engines talk to (results and artifact upload); crane uses the API
# host alone. The live rig tells engine traffic apart by these.
ENGINE_UPLOAD_HOSTS = ("data.blazemeter.com", "storage.blazemeter.com")

# BlazeMeter's public registry: the default DOCKER_REGISTRY.
PUBLIC_REGISTRY = "gcr.io/verdant-bulwark-278"

# Threads one engine runs: BlazeMeter's default for a 2 CPU / 8Gi engine. A
# location with threadsPerEngine unset cannot start a test.
DEFAULT_THREADS_PER_ENGINE = 500

# BlazeMeter's documented engine footprint, used where the bundle pins no
# limits.
ENGINE_DEFAULT_CPU = "2"
ENGINE_DEFAULT_MEM = "8Gi"

# Disk per concurrent engine, in decimal GB as the docs quote it.
ENGINE_DISK_GB = 60
ENGINE_TMP_GB = 40

# Crane's own container resources (the official helm-crane chart's
# resourcesCrane). The scheduler places on the request; a LimitRange judges the
# limit.
CRANE_CPU_REQUEST = "250m"
CRANE_MEM_REQUEST = "512Mi"
CRANE_CPU_LIMIT = "1"
CRANE_MEM_LIMIT = "2Gi"

# Crane's ephemeral storage, used for both request and limit. GKE Autopilot
# rewrites the limit down to the request, and crane uses ~161MiB within seconds
# of starting, so a smaller request got it evicted in a loop; one value binds
# the same on every platform.
CRANE_EPHEMERAL_STORAGE = "1Gi"

# Crane's own engine requests when neither the bundle
# (KUBERNETES_RESOURCES_DEFAULT_CPU/_MEM) nor the location (overrideCPU /
# overrideMemory) sets them. A LimitRange cannot change them: crane sets
# requests explicitly (measured: overrideCPU=1, overrideMemory=4096 gave
# requests {1, 4Gi} against limits {2, 8Gi}).
ENGINE_DEFAULT_REQUEST_CPU = "250m"
ENGINE_DEFAULT_REQUEST_MEM = "256Mi"


def engine_requests(facts, bundle=None):
    """(cpu, memory) an engine pod requests: the location's overrides where
    set, else `bundle` -- the (cpu, memory) the bundle's
    KUBERNETES_RESOURCES_DEFAULT_* carry -- else crane's own default.

    overrideMemory is MB. Accounts hold values like 32, 4000 and 8196, so it is
    reported as found and never rescaled.
    """
    cpu = facts.get("override_cpu")
    mem = facts.get("override_memory")
    fallback = bundle or (ENGINE_DEFAULT_REQUEST_CPU, ENGINE_DEFAULT_REQUEST_MEM)
    return (f"{cpu}" if cpu else fallback[0],
            f"{mem}Mi" if mem else fallback[1])


# The smallest overrideMemory (MB) read as an engine size. The field's unit is
# unreliable and a derived 32Mi limit is an engine OOMKilled at start, so below
# this the default applies. An explicit engine_mem_limit is never floored.
ENGINE_MIN_DERIVED_MEM_MB = 1024

# What a managed node reserves for itself (kubelet reservations and the
# eviction threshold) before any pod: a working allowance for sizing advice,
# not a figure to render a manifest from.
NODE_OVERHEAD_CPU = 1000        # millicores
NODE_OVERHEAD_MEM = 2 * (1024 ** 3)

# System pods on a node of a tainted pool, counted into maxPods. Measured on
# GKE 1.35: 4 DaemonSet pods plus kube-proxy and a managed-prometheus
# collector, though `kubectl get ds -A` lists 32 (most are variants gated by
# nodeAffinity). The taint keeps kube-dns, metrics-server and similar
# Deployments off.
TYPICAL_SYSTEM_PODS = 6

# GKE refuses --max-pods-per-node below 8 (verified against the API), so a GKE
# node always has room for more than one engine.
GKE_MIN_MAX_PODS = 8
