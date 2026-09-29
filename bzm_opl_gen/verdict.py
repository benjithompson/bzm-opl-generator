"""The verdict vocabulary shared by doctor and toolcheck, and their report.

FAIL means the thing asked about would not work; WARN means it works and bites
later, or could not be judged.
"""

import collections

Check = collections.namedtuple("Check", "name status detail")
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def has_failures(checks):
    return any(c.status == FAIL for c in checks)


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def summary_line(checks, consequence):
    """The counts in one sentence, ending with what a failure costs -- stated
    only where something FAILed, so an all-warnings report is not a rejection."""
    counts = collections.Counter(c.status for c in checks)
    line = (f"{counts[PASS]} passed, {_plural(counts[WARN], 'warning')}, "
            + (_plural(counts[FAIL], "failure") if counts[FAIL]
               else "no failures"))
    return f"{line} — {consequence}" if counts[FAIL] else line


def report(header, checks, consequence):
    """Print a header, one aligned row per check, and the summary line."""
    print(header)
    width = max((len(c.name) for c in checks), default=0)
    for c in checks:
        print(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
    print(summary_line(checks, consequence))
