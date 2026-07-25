"""Kubernetes resource quantities as numbers.

Sizing checks have to compare values that arrive in different shapes: the
customer writes engine limits as "2" and "8Gi", a node reports allocatable as
"4" and "6088480Ki", and crane's own template uses "250m" and "512Mi". Parsing
them to millicores / bytes is the only way those comparisons mean anything.
"""

import re

_SUFFIX_BYTES = {
    "": 1,
    "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4,
    "Pi": 1024 ** 5, "Ei": 1024 ** 6,
    "k": 10 ** 3, "M": 10 ** 6, "G": 10 ** 9, "T": 10 ** 12,
    "P": 10 ** 15, "E": 10 ** 18,
}
_QUANTITY = re.compile(r"^(\d+(?:\.\d+)?)([A-Za-z]*)$")


def _split(value, what):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), ""
    m = _QUANTITY.match(str(value or "").strip())
    if not m:
        raise ValueError(f"not a Kubernetes {what} quantity: {value!r}")
    return float(m.group(1)), m.group(2)


def parse_cpu(value):
    """CPU quantity -> millicores. '1' -> 1000, '500m' -> 500."""
    number, suffix = _split(value, "CPU")
    if suffix == "m":
        return int(number)
    if suffix:
        raise ValueError(f"not a Kubernetes CPU quantity: {value!r}")
    return int(number * 1000)


def parse_memory(value):
    """Memory (or ephemeral-storage) quantity -> bytes. '8Gi' -> 8589934592."""
    number, suffix = _split(value, "memory")
    # 'm' is a legal k8s suffix (milli-bytes) but never intentional here: it
    # means someone pasted a CPU value into a memory field, and silently
    # accepting 0.5 bytes would turn that into a check that always passes.
    if suffix not in _SUFFIX_BYTES:
        raise ValueError(f"not a Kubernetes memory quantity: {value!r}")
    return int(number * _SUFFIX_BYTES[suffix])


def format_memory(nbytes):
    """Bytes -> the largest binary suffix that keeps it a whole number."""
    for suffix in ("Ti", "Gi", "Mi", "Ki"):
        unit = _SUFFIX_BYTES[suffix]
        if nbytes >= unit and nbytes % unit == 0:
            return f"{nbytes // unit}{suffix}"
    return str(int(nbytes))


def human_memory(nbytes):
    """Bytes -> a readable approximation. For measured values (node allocatable,
    quota headroom), which rarely land on a round binary boundary -- use
    format_memory for anything that goes into a manifest."""
    for suffix in ("Ti", "Gi", "Mi", "Ki"):
        unit = _SUFFIX_BYTES[suffix]
        if nbytes >= unit:
            return f"{round(nbytes / unit, 1):g}{suffix}"
    return str(int(nbytes))


def format_cpu(millicores):
    if millicores % 1000 == 0:
        return str(millicores // 1000)
    return f"{millicores}m"
