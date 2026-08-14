"""Shim: canonical implementation at invest-a-gap-scan/scripts/lib/kline_source.py."""
from __future__ import annotations

from _invest_path import ensure_shared_lib_on_path  # noqa: E402, F401
from invest_path import load_gap_scan_module  # noqa: E402

_canonical = load_gap_scan_module("kline_source")

globals().update(
    {k: v for k, v in vars(_canonical).items() if not k.startswith("__")}
)
