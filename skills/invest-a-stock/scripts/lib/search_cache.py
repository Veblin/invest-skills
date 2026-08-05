"""WebSearch 结果缓存（30 天 TTL）— 供分析 Agent 复用搜索结果，避免同标的重复搜索。

背景：四视角分析 Agent 每轮深挖做 3-6 轮 WebSearch（网络往返串行，是分析阶段
最大耗时项）。同标的 30 天内重复分析时，缓存命中即可跳过搜索。

路径: {STORE_DIR}/search_cache/{symbol}/{sha1(query)[:12]}.json
TTL: 30 天（mtime）；损坏/过期视为 miss。

CLI 用法（Agent 通过 Bash 调用）:
  uv run python skills/invest-a-stock/scripts/lib/search_cache.py get 300981 "丁腈手套 价格 2026 涨价"
      # 命中 → 打印 JSON {query, results:[{url,title,snippet}], fetched_at}；未命中/过期 → 无输出 exit 1
  uv run python skills/invest-a-stock/scripts/lib/search_cache.py put 300981 "丁腈手套 价格 2026 涨价" /tmp/results.json
      # /tmp/results.json: [{"url": "...", "title": "...", "snippet": "..."}]  → 写入缓存
  uv run python skills/invest-a-stock/scripts/lib/search_cache.py list 300981
      # 列出该标的全部缓存查询
  uv run python skills/invest-a-stock/scripts/lib/search_cache.py cleanup
      # 清理全部过期条目
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

CACHE_TTL_SEC = 30 * 86400  # 30 天


def _cache_root() -> Path:
    """惰性解析 STORE_DIR（模块可被直接运行，顶层不做包内导入）。"""
    try:
        from . import env
    except ImportError:  # 直接运行兜底：__main__ 引导后以 lib.search_cache 重导入
        from lib import env
    return env.STORE_DIR / "search_cache"


def enabled() -> bool:
    """缓存开关。INVEST_SEARCH_CACHE=0 禁用。"""
    return os.environ.get("INVEST_SEARCH_CACHE", "1") != "0"


def _query_hash(query: str) -> str:
    return hashlib.sha1(query.strip().encode("utf-8")).hexdigest()[:12]


def _path(symbol: str, query: str) -> Path:
    return _cache_root() / symbol / f"{_query_hash(query)}.json"


def get(symbol: str, query: str) -> list[dict[str, str]] | None:
    """读取缓存结果；未启用/不存在/过期/损坏 → None。"""
    if not enabled():
        return None
    path = _path(symbol, query)
    try:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > CACHE_TTL_SEC:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        results = data.get("results", [])
        return results if isinstance(results, list) and results else None
    except Exception:
        return None


def put(symbol: str, query: str, results: list[dict[str, str]]) -> None:
    """写入缓存结果；失败不影响调用方。"""
    if not enabled() or not results:
        return
    path = _path(symbol, query)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "query": query,
            "results": results,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        # 原子写入：tmp + rename，防并发读半截文件
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.rename(path)
    except Exception:
        pass


def _valid_results(results: Any) -> bool:
    """与 get() 的命中口径一致：非空 list，每项为 dict 且 url/title 非空。

    CLI 写入未校验的载荷（如单 dict）会被 get() 判定为永久 miss 达
    CACHE_TTL_SEC 天——agent 以为搜索已缓存实则每次都重搜。
    """
    if not isinstance(results, list) or not results:
        return False
    for r in results:
        if not isinstance(r, dict):
            return False
        if not (str(r.get("url") or "").strip() and str(r.get("title") or "").strip()):
            return False
    return True


def list_queries(symbol: str) -> list[str]:
    """列出该标的已缓存的查询。"""
    d = _cache_root() / symbol
    if not d.exists():
        return []
    out: list[str] = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if time.time() - f.stat().st_mtime <= CACHE_TTL_SEC:
                out.append(data.get("query", f.stem))
        except Exception:
            continue
    return out


def cleanup() -> int:
    """清理过期条目，返回删除数。"""
    root = _cache_root()
    if not root.exists():
        return 0
    now = time.time()
    removed = 0
    for f in root.rglob("*.json"):
        if now - f.stat().st_mtime > CACHE_TTL_SEC:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "get" and len(argv) == 4:
        results = get(argv[2], argv[3])
        if results:
            print(json.dumps({"query": argv[3], "results": results},
                             ensure_ascii=False))
            return 0
        return 1
    if cmd == "put" and len(argv) == 5:
        try:
            results = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"读取结果文件失败: {exc}", file=sys.stderr)
            return 2
        if not _valid_results(results):
            print(
                "缓存内容无效：需为非空 JSON 数组，且每项含非空 url 与 title",
                file=sys.stderr,
            )
            return 2
        put(argv[2], argv[3], results)
        print("ok")
        return 0
    if cmd == "list" and len(argv) == 3:
        for q in list_queries(argv[2]):
            print(q)
        return 0
    if cmd == "cleanup":
        print(f"removed {cleanup()}")
        return 0
    print(f"未知命令: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    # 直接运行兜底（python lib/search_cache.py 时无包上下文）：
    # 把 scripts/ 加入 sys.path 后以包形式重新导入自身，使 `from . import env` 可用
    _here = Path(__file__).resolve()
    _scripts_dir = str(_here.parents[1])
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from lib.search_cache import _main  # noqa: E402

    sys.exit(_main(sys.argv))
