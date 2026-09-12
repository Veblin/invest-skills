"""快照 JSONL 读写（DS-1 / T12-2，设计 §6 + §8）。

落点：``~/.local/share/investment/discovery/{YYYY}.jsonl``（**私有目录**，同 research.db
惯例；个股产出红线：不进仓库、不进公开面）。

`rules_version` 是回填裁决的**唯一锚点**（设计 §8-4「禁止未记录原因改参」）→
缺失即 fail loud，不写出无法归因的快照。空 `hits` **照写**——「本次无命中」是有信息量的
结果，不落盘会让回填无法区分「没跑」与「跑了没命中」。
"""
from __future__ import annotations

import json
from pathlib import Path

RULES_VERSION = "0.1.0"        # 阈值/规则改动必须 bump（设计 §8-4）

_REQUIRED_TOP = ("snapshot_ts", "rules_version", "trade_date", "pool", "params", "hits")
_REQUIRED_POOL = ("market", "n_positive_pe", "n_pool")
_REQUIRED_PARAMS = ("pe_grank_max", "ind_rank_max", "roe_min", "top_n",
                    "per_industry", "with_bj")
_REQUIRED_HIT = ("ts_code", "name", "industry", "pe_ttm", "ey_pct", "pe_grank",
                 "ind_rk", "ind_n", "gap_flags", "mv_yi", "close", "fillback")


def discovery_dir() -> Path:
    """私有快照目录（可用 `INVEST_DISCOVERY_DIR` 覆盖，便于测试与迁移）。"""
    import os

    override = os.environ.get("INVEST_DISCOVERY_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "investment" / "discovery"


def snapshot_path(year: int | None = None) -> Path:
    """``{YYYY}.jsonl``（默认按当前北京年）。"""
    if year is None:
        from dates import shanghai_today

        year = int(shanghai_today()[:4])
    return discovery_dir() / f"{year}.jsonl"


def build_snapshot(*, scan_ts: str, trade_date: str, pool: dict, params: dict,
                   hits: list[dict], warnings: list[str] | None = None) -> dict:
    """组装一份快照记录（schema 照设计 §6）。"""
    return {
        "snapshot_ts": scan_ts,
        "rules_version": RULES_VERSION,
        "trade_date": trade_date,
        "pool": pool,
        "params": params,
        "hits": [dict(h) for h in (hits or [])],
        "warnings": list(warnings or []),
    }


def validate_snapshot(rec: dict) -> list[str]:
    """返回错误清单（空 = 合法）。`rules_version` 缺失单独点名。"""
    errs: list[str] = []
    if not isinstance(rec, dict):
        return ["快照记录不是 dict"]
    for key in _REQUIRED_TOP:
        if key not in rec:
            errs.append(f"缺字段或为空: {key}")
            continue
        val = rec[key]
        # ⚠️ 空 `hits` / 空 `warnings` 是**合法**值（「本次无命中」有信息量）——
        # 用 `if not val` 会把空列表误判为缺失（falsy 陷阱）
        if val is None or (isinstance(val, str) and not val.strip()):
            errs.append(f"缺字段或为空: {key}")
    if not rec.get("rules_version"):
        errs.append("rules_version 必录（回填裁决的锚点，缺失则快照无法归因）")
    if isinstance(rec.get("pool"), dict):
        errs += [f"pool 缺字段: {k}" for k in _REQUIRED_POOL if k not in rec["pool"]]
    if isinstance(rec.get("params"), dict):
        errs += [f"params 缺字段: {k}" for k in _REQUIRED_PARAMS if k not in rec["params"]]
    for i, hit in enumerate(rec.get("hits") or []):
        errs += [f"hits[{i}] 缺字段: {k}" for k in _REQUIRED_HIT if k not in hit]
    return errs


def append_snapshot(rec: dict, *, path: Path | None = None) -> Path:
    """追加一行 JSONL；**校验失败即 raise，不写半份快照**。"""
    errs = validate_snapshot(rec)
    if errs:
        raise ValueError("快照校验失败：" + "；".join(errs))
    p = Path(path) if path else snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


def read_snapshots(*, year: int | None = None, path: Path | None = None) -> list[dict]:
    """读回快照（文件不存在 → 空列表；坏行跳过不抛——历史数据不应阻断读取）。"""
    p = Path(path) if path else snapshot_path(year)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
