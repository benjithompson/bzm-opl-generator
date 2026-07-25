"""Kubernetes quantity parsing -- the arithmetic behind the LimitRange
validation and the doctor capacity checks."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import quantity as q  # noqa: E402


@pytest.mark.parametrize("value,millicores", [
    ("1", 1000),
    ("2", 2000),
    ("500m", 500),
    ("250m", 250),
    (2, 2000),
    (0.5, 500),
    ("1500m", 1500),
])
def test_parse_cpu(value, millicores):
    assert q.parse_cpu(value) == millicores


@pytest.mark.parametrize("value,by", [
    ("8Gi", 8 * 1024 ** 3),
    ("512Mi", 512 * 1024 ** 2),
    # Node allocatable is reported in Ki, not Gi.
    ("6088480Ki", 6088480 * 1024),
    ("1G", 10 ** 9),
    ("100M", 100 * 10 ** 6),
    ("1024", 1024),
    ("1Ti", 1024 ** 4),
    (2048, 2048),
])
def test_parse_memory(value, by):
    assert q.parse_memory(value) == by


@pytest.mark.parametrize("bad", ["", None, "eight", "8Gb", "1.2.3", "Gi"])
def test_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        q.parse_memory(bad)
    with pytest.raises(ValueError):
        q.parse_cpu(bad)


def test_memory_rejects_cpu_style_millis():
    """'500m' is 0.5 bytes in k8s terms -- as a memory quantity it is always a
    mistake (someone pasted a CPU value), so refuse it rather than round it."""
    with pytest.raises(ValueError):
        q.parse_memory("500m")


def test_format_memory_is_round_trippable():
    assert q.format_memory(8 * 1024 ** 3) == "8Gi"
    assert q.format_memory(512 * 1024 ** 2) == "512Mi"
    assert q.parse_memory(q.format_memory(40960 * 1024 ** 2)) == 40960 * 1024 ** 2


def test_human_memory_rounds_what_format_memory_cannot():
    """Node allocatable is rarely a round binary number -- 12220836Ki has no
    exact Gi form, and printing it back as Ki is unreadable in a report."""
    assert q.human_memory(q.parse_memory("12220836Ki")) == "11.7Gi"
    assert q.human_memory(8 * 1024 ** 3) == "8Gi"
    assert q.human_memory(512 * 1024 ** 2) == "512Mi"
    assert q.human_memory(0) == "0"


def test_format_cpu():
    assert q.format_cpu(2000) == "2"
    assert q.format_cpu(500) == "500m"
    assert q.format_cpu(1500) == "1500m"
