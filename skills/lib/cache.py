"""通用 TTL 缓存管理器。JSON 文件存储，所有 skill 共享。

缓存目录: ``~/.local/share/investment/cache/{dimension}/{symbol}.json``

.. code-block:: json

    {
      "key": "kline:600176:2026-07-26",
      "data": [...],
      "fetched_at_epoch": 1721545200.0,
      "ttl_seconds": 14400,
      "source": "tickflow",
      "symbol": "600176",
      "dimension": "kline",
      "version": 1
    }
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 缓存根目录
_CACHE_DIR = Path.home() / ".local" / "share" / "investment" / "cache"

# 缓存格式版本
_CACHE_VERSION = 1

# LRU 清理阈值
_MAX_ENTRIES = 500


def _is_trading_hour() -> bool:
    """A 股交易时段判断（9:30–11:30, 13:00–15:00，仅工作日）。

    简单判断：不考虑节假日（TTL 略微保守不是致命错误）。
    """
    from zoneinfo import ZoneInfo

    try:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        # 降级：假定为盘中（更保守）
        return True

    if now.weekday() >= 5:  # 周六/周日
        return False

    t = now.time()
    morning = dt_time(9, 30) <= t <= dt_time(11, 30)
    afternoon = dt_time(13, 0) <= t <= dt_time(15, 0)
    return morning or afternoon


class DataCache:
    """TTL 缓存管理器，按维度 + 标的读写 JSON 缓存文件。"""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else _CACHE_DIR
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()
        self._set_count = 0

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def get(self, dimension: str, symbol: str, *,
            max_age_seconds: int | None = None) -> dict | None:
        """读取缓存条目。

        Parameters
        ----------
        dimension : str
            维度名（kline / quote / financials / macro …）。
        symbol : str
            标的代码或指标名。
        max_age_seconds : int | None
            覆盖默认 TTL 的自定义最大年龄。为 None 时使用条目自带 TTL。

        Returns
        -------
        dict or None
            缓存条目中的 ``data`` 字段；不存在或过期返回 None。
        """
        path = self._cache_path(dimension, symbol)
        if not path.exists():
            with self._lock:
                self._misses += 1
            return None

        try:
            entry = self._load(path)
        except (json.JSONDecodeError, OSError):
            # 不在此处删除文件：_load 在锁外执行，删文件可能误伤并发的 set()
            # 损坏/空文件留给 LRU 清理或下次 set() 覆盖
            with self._lock:
                self._misses += 1
            return None

        if entry is None:
            with self._lock:
                self._misses += 1
            return None

        # 版本不匹配视为过期（不删除文件，留给 LRU 清理；避免与 set() 的并发写入竞态）
        if entry.get("version") != _CACHE_VERSION:
            with self._lock:
                self._misses += 1
            return None

        # 检查过期
        if max_age_seconds is not None:
            fetched_at = entry.get("fetched_at_epoch", 0)
            if not isinstance(fetched_at, (int, float)):
                with self._lock:
                    self._misses += 1
                return None  # 损坏的缓存文件
            age = time.time() - fetched_at
            if age > max_age_seconds:
                with self._lock:
                    self._misses += 1
                return None
        elif self._is_expired(entry):
            with self._lock:
                self._misses += 1
            return None

        with self._lock:
            self._hits += 1
        data = entry.get("data")
        # 仅对 dict 类型标记 _from_cache（list/其他类型不标记，避免类型不一致）。
        # shallow-copy 防止 _from_cache 被 caller 持久化到 JSON 缓存文件。
        # 注意：嵌套的 list/dict 仍与缓存条目共享引用；caller 如需修改嵌套
        # 结构必须自行 deepcopy。当前所有 caller 仅写入顶层 _from_cache 键，
        # 因此 shallow copy 足够。
        if isinstance(data, dict):
            data = dict(data)
            data["_from_cache"] = True
        return data

    def set(self, dimension: str, symbol: str, data: Any, *,
            ttl_seconds: int, source: str = "") -> None:
        """写入缓存条目（原子写入）。

        Parameters
        ----------
        dimension : str
            维度名。
        symbol : str
            标的代码。
        data : Any
            要缓存的原始数据（JSON 可序列化）。
        ttl_seconds : int
            基准 TTL（秒）。
        source : str
            数据来源标记。
        """
        path = self._cache_path(dimension, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)

        entry: dict[str, Any] = {
            "key": f"{dimension}:{symbol}:{datetime.now().strftime('%Y-%m-%d')}",
            "data": data,
            "fetched_at_epoch": time.time(),
            "ttl_seconds": ttl_seconds,
            "source": source,
            "symbol": symbol,
            "dimension": dimension,
            "version": _CACHE_VERSION,
        }

        import logging as _cache_logging
        _cache_log = _cache_logging.getLogger(__name__)

        def _json_default(obj):
            # numpy/pandas 标量转原生类型（惰性 import，不加重模块顶层依赖）：
            # 否则 np.int64 等经 str() 写回后读出来是字符串，下游数值计算错乱
            try:
                import numpy as _np  # noqa: PLC0415

                # timedelta64 是 signedinteger 子类，必须先于 integer 分支判断
                if isinstance(obj, _np.timedelta64):
                    # item() 会返回 datetime.timedelta（C 编码器不再递归 default → 崩溃）
                    return float(obj / _np.timedelta64(1, "s"))  # → 秒数
                if isinstance(obj, _np.integer):
                    return int(obj)
                if isinstance(obj, _np.floating):
                    return float(obj)
                if isinstance(obj, _np.bool_):
                    return bool(obj)
                if isinstance(obj, _np.ndarray):
                    return obj.tolist()
                if isinstance(obj, _np.generic):
                    item = obj.item()
                    # item() 可能返回 timedelta/complex 等仍不可序列化类型 → 兜底 str()
                    if item is None or isinstance(item, (int, float, bool, str, list, dict)):
                        return item
                    return str(item)
            except ImportError:
                pass
            try:
                import pandas as _pd  # noqa: PLC0415

                if isinstance(obj, _pd.Timestamp):
                    return obj.isoformat()
            except ImportError:
                pass
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            _cache_log.warning(
                "cache: non-JSON-serializable type %s serialized via str() — "
                "data may be lossy on read-back", type(obj).__name__
            )
            return str(obj)

        # 原子写入 + LRU 清理：持锁防止与 get() 的 unlink 竞态
        tmp_path = path.with_suffix(".tmp")
        with self._lock:
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False, default=_json_default)
                tmp_path.rename(path)
            except OSError:
                tmp_path.unlink(missing_ok=True)
                raise

            # LRU 清理：每 50 次写入执行一次，避免每次 O(N log N) 文件扫描
            self._set_count += 1
            if self._set_count % 50 == 0:
                self._lru_cleanup_locked()

    def invalidate(self, dimension: str | None = None,
                   symbol: str | None = None) -> int:
        """清除缓存。

        - 同时指定 dimension + symbol → 删除单个文件
        - 仅指定 dimension → 删除整个维度目录
        - 都不指定 → 清空全部缓存

        Returns
        -------
        int
            删除的文件数。
        """
        count = 0
        if dimension is None:
            # 清空全部
            if self._cache_dir.exists():
                for f in self._cache_dir.rglob("*.json"):
                    f.unlink(missing_ok=True)
                    count += 1
            return count

        if symbol is not None:
            path = self._cache_path(dimension, symbol)
            if path.exists():
                path.unlink(missing_ok=True)
                return 1
            return 0

        # 清除整个维度目录
        dim_dir = self._cache_dir / dimension
        if dim_dir.exists():
            for f in dim_dir.rglob("*.json"):
                try:
                    f.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass  # 并发删除
                count += 1
        return count

    def stats(self) -> dict:
        """返回缓存统计。

        Returns
        -------
        dict
            total_entries / total_size_mb / dimension_distribution / hits / misses / hit_rate。
        """
        total_files = 0
        total_size = 0
        dim_dist: dict[str, int] = {}

        if self._cache_dir.exists():
            for f in self._cache_dir.rglob("*.json"):
                if f.is_file() and f.suffix == ".json":
                    total_files += 1
                    total_size += f.stat().st_size
                    dim = f.parent.name
                    dim_dist[dim] = dim_dist.get(dim, 0) + 1

        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0

        return {
            "total_entries": total_files,
            "total_size_mb": round(total_size / 1e6, 2),
            "dimension_distribution": dim_dist,
            "session_hits": self._hits,
            "session_misses": self._misses,
            "session_hit_rate": f"{hit_rate:.1%}",
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _cache_path(self, dimension: str, symbol: str) -> Path:
        """缓存文件路径: &lt;cache_dir&gt;/&lt;dimension&gt;/&lt;symbol&gt;.json"""
        # symbol 中可能含路径分隔符，替换为安全字符
        safe_symbol = symbol.replace("/", "_").replace("\\", "_")
        return self._cache_dir / dimension / f"{safe_symbol}.json"

    @staticmethod
    def _load(path: Path) -> dict | None:
        """加载并验证缓存文件。损坏或并发删除返回 None。"""
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
        except FileNotFoundError:
            return None  # 并发 _lru_cleanup 已删除此文件
        if not isinstance(entry, dict) or "data" not in entry:
            return None
        return entry

    def _is_expired(self, entry: dict) -> bool:
        """判断缓存条目是否过期。"""
        fetched_at = entry.get("fetched_at_epoch", 0)
        if not isinstance(fetched_at, (int, float)):
            return True  # 损坏的缓存文件，视为过期
        age = time.time() - fetched_at
        effective = self._effective_ttl(entry)
        return age >= effective

    def _effective_ttl(self, entry: dict) -> float:
        """根据当前市场状态返回有效 TTL。

        盘中（9:30–15:00）：TTL × 0.8（更频繁刷新）
        盘后/周末：TTL × 2.0（数据不变）
        """
        base = entry.get("ttl_seconds", 3600)
        if _is_trading_hour():
            return float(base * 0.8)  # truncate: avoid ms-level boundary flapping
        return float(base * 2.0)

    def _lru_cleanup_locked(self) -> int:
        """超过 _MAX_ENTRIES 时按文件修改时间删除最旧条目（调用方须已持有 self._lock）。"""
        if not self._cache_dir.exists():
            return 0

        def _safe_mtime(f):
            try:
                return f.stat().st_mtime
            except FileNotFoundError:
                return 0.0  # 并发删除：推到列表最前面（最早删除）
        files = sorted(
            self._cache_dir.rglob("*.json"),
            key=_safe_mtime,
        )

        removed = 0
        while len(files) - removed > _MAX_ENTRIES:
            try:
                files[removed].unlink()
                removed += 1
            except OSError:
                removed += 1  # 跳过无法删除的文件

        if removed:
            logger.info("LRU cleanup: removed %d cache entries", removed)
        return removed


# 模块级单例
_default_cache = DataCache()


def default_cache() -> DataCache:
    """获取模块级 DataCache 单例。"""
    return _default_cache
