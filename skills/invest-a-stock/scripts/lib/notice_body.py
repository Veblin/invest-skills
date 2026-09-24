"""公告正文管道（v0.3.1）。

来源链：公告 `url` → `art_code` → 东财内容接口 → 正文文本。

**实测约束（2026-09-24，600176 / 300750 探针）**：

1. 正文**固定 5000 字截断**——`page_size` 改成 10000/50000 均无效，结尾断在句中。
   长公告（如港股「翌日披露报表」整页表格）拿不到全文 → **三态标注**，不假装完整。
   完整原文只在 `attach_url` 的 PDF 镜像里，本版**不引入 PDF 依赖**。
2. 港股公告为**繁体**（「股份購回」）；`'回购' in body` 会因繁简差异为 False。
   这是**调用方**要处理的差异：本模块**不改写原文**（繁体按原样输出），
   需要简体关键词匹配时由调用方自行归一，且不得据此下「文中没有 X」的结论。
3. 接口属东财 → 必须经 `akshare_direct_session()`（清代理 + 连续调用限流 ≥0.5s）。

**本模块只做管道**：取正文 / 截断判定 / 缓存，**不做文本改写**（含繁简转换）——
本命令的用途是溯源，转换会让回收到的正文与交易所原文不再一致。
结构化抽取规则（回购金额、中标金额等）**不在本版**——见 v0.3.1 公告工作包 D/E 的后置部分。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_API = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
_ART_CODE_RE = re.compile(r"(AN\d{10,})")
#: 整串校验用（含首尾锚定）：用于文件名与 URL 参数，防路径穿越/注入
_ART_CODE_FULL = re.compile(r"\AAN\d{10,}\Z")

#: 实测的正文上限——达到该长度即视为已截断（保守：恰好 5000 字的完整正文极少）
BODY_CHAR_CAP = 5000

def extract_art_code(url: str) -> str | None:
    """从公告 url 里取出东财 `art_code`（形如 `AN202609171829519061`）。"""
    if not url:
        return None
    m = _ART_CODE_RE.search(str(url))
    return m.group(1) if m else None


def _body_cache_dir() -> Path:
    from . import env
    return env.STORE_DIR / "notice_bodies"


def _safe_art_code(art_code: str) -> str | None:
    """规范化并校验 art_code。

    `_cache_path` 会把它插进文件名——不校验的话，`"../../escaped"` 这类值能写到
    缓存目录之外（公开 API 不能假设调用方只传自己提取出来的 id）。
    """
    token = str(art_code or "").strip()
    return token if _ART_CODE_FULL.match(token) else None


def _cache_path(art_code: str) -> Path:
    safe = _safe_art_code(art_code)
    if safe is None:
        raise ValueError(f"invalid art_code: {art_code!r}")
    return _body_cache_dir() / f"{safe}.json"


def read_cached_body(art_code: str) -> dict[str, Any] | None:
    """读正文缓存；缺失、损坏或 art_code 非法一律返回 None（不抛）。"""
    if _safe_art_code(art_code) is None:
        return None
    p = _cache_path(art_code)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("notice_body: cache unreadable for %s: %s", art_code, exc)
        return None
    return d if isinstance(d, dict) else None


def write_cached_body(art_code: str, payload: dict[str, Any]) -> None:
    """写正文缓存；art_code 非法或写入失败仅记日志（缓存不是正确性前提）。"""
    if _safe_art_code(art_code) is None:
        logger.warning("notice_body: refuse to cache invalid art_code %r", art_code)
        return
    p = _cache_path(art_code)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("notice_body: cache write failed for %s: %s", art_code, exc)


def fetch_notice_body(
    art_code: str,
    *,
    timeout: float = 20.0,
    use_cache: bool = True,
) -> dict[str, Any]:
    """取一条公告的正文。

    Returns:
        dict，字段：
        - ``status``: ``"ok"`` / ``"error"`` / ``"missing"``
        - ``text``: 接口返回的**原文**（繁体按原样保留，不做繁简转换；
          ``status != "ok"`` 时为空串）
        - ``char_count`` / ``truncated``: 截断判定（``truncated`` 为保守真值）
        - ``source`` / ``art_code`` / ``fetched_at`` / ``error``
        - ``cached``: ``True`` 表示命中本地缓存。此时 ``fetched_at`` 是**首次**取数
          时刻、不是本次读取时刻，展示时须区分（本模块是溯源命令，不得让旧正文冒充新取）

    ``truncated=True`` 表示**正文可能不完整**——调用方不得据此下「文中没有 X」的结论，
    只能标「未在可见正文中发现 X（正文截断）」。
    """
    safe = _safe_art_code(art_code)
    if safe is None:
        return {"status": "missing", "art_code": str(art_code or ""), "text": "",
                "char_count": 0, "truncated": False, "source": "",
                "error": "invalid or missing art_code"}
    art_code = safe

    if use_cache:
        cached = read_cached_body(art_code)
        if cached and cached.get("status") == "ok":
            # 打标：调用方据此区分「本次取数」与「读本地缓存」——缓存里的 fetched_at
            # 是**首次**取数时刻，直接当「取数时刻」展示会让旧正文冒充新取。
            out = dict(cached)
            out["cached"] = True
            return out

    from .proxy import akshare_direct_session

    url = f"{_API}?art_code={art_code}&client_source=web&page_index=1"
    try:
        with akshare_direct_session():
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
        payload = json.loads(raw).get("data") or {}
        body = str(payload.get("notice_content") or "")
        result: dict[str, Any] = {
            "status": "ok" if body else "missing",
            "art_code": art_code,
            "text": body,
            "char_count": len(body),
            "truncated": len(body) >= BODY_CHAR_CAP,
            "source": "eastmoney np-cnotice api/content/ann",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "error": "" if body else "empty notice_content",
        }
    except Exception as exc:  # noqa: BLE001 —— 单条取数失败不得中断整批
        logger.warning("notice_body: fetch failed for %s: %s", art_code, exc)
        return {"status": "error", "art_code": art_code, "text": "", "char_count": 0,
                "truncated": False, "source": "", "fetched_at": "", "cached": False,
                "error": f"{type(exc).__name__}: {exc}"}

    if result["status"] == "ok":
        write_cached_body(art_code, result)
    return result


def fetch_body_by_url(url: str, **kwargs: Any) -> dict[str, Any]:
    """按公告 url 取正文（等价于 ``extract_art_code`` + ``fetch_notice_body``）。"""
    return fetch_notice_body(extract_art_code(url) or "", **kwargs)


def describe(body: dict[str, Any]) -> str:
    """三态中文字面（供渲染层直接引用，避免各处各写一套措辞）。"""
    st = body.get("status")
    if st == "ok":
        if body.get("truncated"):
            return f"正文已取（{body.get('char_count')} 字，**达接口上限、可能截断**）"
        return f"正文已取（{body.get('char_count')} 字）"
    if st == "missing":
        return "正文不可得（接口未返回内容）"
    return f"正文不可得（{body.get('error') or '未知错误'}）"


__all__ = [
    "BODY_CHAR_CAP", "extract_art_code",
    "fetch_notice_body", "fetch_body_by_url", "describe",
    "read_cached_body", "write_cached_body",
]
