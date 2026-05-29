# -*- coding: utf-8 -*-
"""
Regression test for the ETA formatter in msgviz.core.progress.

Locks in the format we render on the progress line so a future tweak
doesn't accidentally start printing "0 days, 1:02:03" or similar
verbose forms.
"""
from __future__ import annotations

import pytest

from msgviz.core.progress import _fmt_eta


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-5, "0s"),                # negative → clamp to 0s
        (0, "0s"),
        (1, "1s"),
        (12.3, "12s"),             # sub-minute: just seconds, rounded
        (45.7, "46s"),             # rounding
        (60, "1m 0s"),             # minute boundary
        (67, "1m 7s"),
        (910, "15m 10s"),          # minutes + seconds
        (3599, "59m 59s"),         # just under an hour
        (3600, "1h 0m"),           # exactly an hour → drop seconds
        (4200, "1h 10m"),          # > 1 hour: hours + minutes only
        (10800, "3h 0m"),
        (99999, "27h 46m"),        # don't promote to days
    ],
)
def test_fmt_eta(seconds: float, expected: str) -> None:
    assert _fmt_eta(seconds) == expected
