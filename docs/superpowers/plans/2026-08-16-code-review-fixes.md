# /code-review max 10 项修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-08-15 `/code-review max` 的 10 项 CONFIRMED finding（规格：`docs/superpowers/specs/2026-08-15-code-review-fixes-design.md`，用户已批准：全部一轮修完 + OI 指标从用户标签移除 + F1/F2 分位 look-ahead 纳入）。

**Architecture:** 数据层（futures_data）按"前合约到期日"划分当月合约窗口并新增共享 `compound_oi_change` helper；E/H6/F 系列脚本做局部修复；journal/ETF/pulse 消费方移除 OI 20 日指标输出；ADX 测试改为独立手算 golden oracle；随后 `--force` 回填重建 DB、重跑 F1/F2/F3/E/H6、同步全部 JSON 与文档。

**Tech Stack:** Python 3.12 + pandas + pytest；Tushare fut_daily/fut_basic（80/min 限速）+ akshare 指数日线；sqlite（`lib.store`）。所有运行用 `uv run python`。

**Spec:** `docs/superpowers/specs/2026-08-15-code-review-fixes-design.md` — 本文档与其逐节对应（A→Task2/8、B→Task1/5/6、C→Task3/10、D→Task4/11、E→Task5/9、F→Task7、G→Task9/10/11/12）。

---

### Task 1: `compound_oi_change` 共享 helper（finding #10）

**Files:**
- Modify: `skills/invest-a-stock/scripts/lib/futures_data.py`（追加函数）
- Test: `skills/invest-a-stock/tests/test_futures_data.py`（追加类）

- [ ] **Step 1: 写失败测试** — 在 `test_futures_data.py` 末尾追加：

```python
class TestCompoundOiChange:
    def test_all_valid_20(self):
        assert fd.compound_oi_change([1.0] * 20) == pytest.approx(22.019004, abs=1e-4)

    def test_masked_and_none_excluded_from_count_and_product(self):
        # 18 个 +1% + 1 个 -100（到期塌缩掩码）+ 1 个 None → 有效 18 ≥ 18 → 18 日复利
        assert fd.compound_oi_change([1.0] * 18 + [-100.0, None]) == pytest.approx(19.614748, abs=1e-4)

    def test_below_min_valid_returns_none(self):
        assert fd.compound_oi_change([1.0] * 17 + [None] * 3) is None

    def test_window_trims_to_tail(self):
        assert fd.compound_oi_change([5.0] * 5 + [1.0] * 15) == pytest.approx(48.172327, abs=1e-4)

    def test_nan_excluded(self):
        # DB 经 DataFrame 读取时 None 变 NaN——NaN 与 None 同等待遇
        assert fd.compound_oi_change([float("nan")] * 2 + [1.0] * 18) == pytest.approx(22.019004, abs=1e-4)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest skills/invest-a-stock/tests/test_futures_data.py::TestCompoundOiChange -q`
Expected: FAIL（`AttributeError: module 'lib.futures_data' has no attribute 'compound_oi_change'`）

- [ ] **Step 3: 实现** — 在 `futures_data.py` 的 `compute_basis` 之后追加：

```python
def compound_oi_change(
    vals: list[float | None], *, window: int = 20, min_valid: int = 18,
) -> float | None:
    """尾部 window 个日环比（oi_change_pct）复利合成 N 日持仓变化。

    口径唯一实现（run_f3 / 各消费方共用，禁止复制粘贴）：
    None 或 <= -99（到期日 OI 归零机械塌缩掩码）不计入因子也不计入有效数；
    有效因子数 < min_valid → None（防全缺失窗口伪造 0 变化）。
    返回原始百分比（不 round；调用方按需 round）。
    """
    w = vals[-window:]
    prod = 1.0
    cnt = 0
    for v in w:
        if v is not None and v > -99.0:  # NaN 比较恒 False → 自然排除
            prod *= 1.0 + v / 100.0
            cnt += 1
    if cnt < min_valid:
        return None
    return (prod - 1.0) * 100.0
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run python -m pytest skills/invest-a-stock/tests/test_futures_data.py -q`
Expected: PASS（既有用例 + 新类全绿）

- [ ] **Step 5: Commit**

```bash
git add skills/invest-a-stock/scripts/lib/futures_data.py skills/invest-a-stock/tests/test_futures_data.py
git commit -m "fix: F 系列共享 compound_oi_change helper（OI 复利口径单份实现）+ 测试"
```

---

### Task 2: futures_daily 当月窗口按到期日划分（finding #1）

**Files:**
- Modify: `skills/invest-a-stock/scripts/lib/futures_data.py`（contract_series / fetch_contract / ensure_futures_daily）
- Modify: `skills/invest-a-stock/scripts/lib/store.py`（新增 clear_futures_daily）
- Modify: `scripts/backfill_futures_daily.py`（--force）
- Test: `skills/invest-a-stock/tests/test_futures_data.py`（更新既有 3 处 + 追加 3 类）

- [ ] **Step 1: 写失败测试** — 在 `test_futures_data.py` 修改 `_FakeClient`（fut_basic 增加 last_trade_date 列）并更新/追加：

```python
class _FakeClient:
    def __init__(self):
        self.queries: list[tuple] = []

    def query(self, api_name, **kwargs):
        self.queries.append((api_name, kwargs))
        if api_name == "fut_basic":
            return pd.DataFrame({
                "ts_code": ["IF2608.CFX", "IF2609.CFX", "IC2608.CFX", "IM2608.CFX"],
                "list_date": ["20260701"] * 4,
                "delist_date": ["20260831"] * 4,
                "last_trade_date": ["20260821", "20260918", "20260821", "20260821"],
            })
        if api_name == "fut_daily":
            code = kwargs["ts_code"]
            return pd.DataFrame({
                "trade_date": ["20260814", "20260813"],
                "settle": [4652.4, 4650.0],
                "open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [4648.4, 4646.0],
                "oi": [33117.0, 34433.0],
                "oi_chg": [-1316.0, -900.0],
            })
        return pd.DataFrame()


class _WindowFakeClient(_FakeClient):
    def query(self, api_name, **kwargs):
        if api_name == "fut_daily":
            return pd.DataFrame({
                "trade_date": ["20260825", "20260814", "20260725", "20260710"],
                "settle": [4652.4, 4650.0, 4600.0, 4590.0],
                "open": [1, 1, 1, 1], "high": [1, 1, 1, 1], "low": [1, 1, 1, 1],
                "close": [4648.4, 4646.0, 4596.0, 4586.0],
                "oi": [33117.0] * 4, "oi_chg": [-100.0] * 4,
            })
        return super().query(api_name, **kwargs)


class _FullFakeClient(_FakeClient):
    """每合约返回 2026-06-01..2026-09-30 全部工作日行（模拟完整生命周期）。"""
    def query(self, api_name, **kwargs):
        if api_name == "fut_daily":
            import datetime
            dates = []
            d0 = datetime.date(2026, 6, 1)
            while d0 <= datetime.date(2026, 9, 30):
                if d0.weekday() < 5:
                    dates.append(d0.isoformat().replace("-", ""))
                d0 += datetime.timedelta(days=1)
            n = len(dates)
            return pd.DataFrame({
                "trade_date": dates,
                "settle": [4600.0] * n, "open": [1.0] * n, "high": [1.0] * n,
                "low": [1.0] * n, "close": [4596.0] * n, "oi": [30000.0] * n,
                "oi_chg": [0.0] * n,
            })
        return super().query(api_name, **kwargs)
```

更新既有测试 `TestContractSeries` 与 `TestFetchContractMonthlyUniqueness`（后者删除，替换为窗口语义），追加：

```python
class TestContractSeries:
    def test_series_from_codes(self):
        client = _FakeClient()
        series = fd.contract_series(client)
        assert series["IF"] == [("IF2608.CFX", "2026-08-21"), ("IF2609.CFX", "2026-09-18")]
        assert "IC" in series and "IM" in series
        assert "T1" not in series

    def test_expiry_fallback_third_friday(self):
        # fut_basic 无 last_trade_date/last_ddate → 兜底计算该月第三个周五
        assert fd._third_friday("2608") == "2026-08-21"
        assert fd._third_friday("1504") == "2015-04-17"  # IF1504 真实到期日


class TestFetchContractWindow:
    def test_window_partition(self):
        rows = fd.fetch_contract(_FakeClient(), "IF2608.CFX", "2026-07-17", "2026-08-21")
        assert all("2026-07-17" < r["date"] <= "2026-08-21" for r in rows)
        assert all(r["symbol"] == "IF" for r in rows)
        assert len(rows) == 2

    def test_window_excludes_outside_rows(self):
        rows = fd.fetch_contract(_WindowFakeClient(), "IF2608.CFX", "2026-07-17", "2026-08-21")
        assert [r["date"] for r in rows] == ["2026-08-14", "2026-07-25"]

    def test_front_month_series_no_gap(self):
        """回归（finding #1）：月内到期日→月末的交易日必须由下一合约补齐。"""
        all_rows = []
        contracts = [("IF2607.CFX", "2026-07-17"), ("IF2608.CFX", "2026-08-21"),
                     ("IF2609.CFX", "2026-09-18")]
        client = _FullFakeClient()
        prev = "2026-05-31"
        for code, expiry in contracts:
            rows = fd.fetch_contract(client, code, prev, expiry)
            all_rows.extend(rows)
            prev = expiry
        dates = sorted(r["date"] for r in all_rows)
        assert len(dates) == len(all_rows)  # 相邻合约重叠日只归一份（无重复）
        assert any("2026-07-18" <= d <= "2026-07-31" for d in dates)  # 旧实现此处为洞


class TestClearFuturesDaily:
    def test_clear(self, isolated_store):
        rows = [{
            "date": "2026-08-14", "symbol": "IF", "contract": "IF2608.CFX",
            "open": 1, "high": 1, "low": 1, "close": 4648.4, "settle": 4652.4,
            "oi": 33117.0, "oi_chg": -1316.0,
            "basis_pts": -13.48, "basis_pct": -0.2889, "oi_change_pct": -3.82,
            "source": "tushare",
        }]
        store.save_futures_daily(rows)
        assert store.clear_futures_daily() == 1
        assert store.load_futures_daily(symbol="IF") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest skills/invest-a-stock/tests/test_futures_data.py -q`
Expected: FAIL（`TypeError: fetch_contract() missing 2 required positional arguments` / `_third_friday` 不存在 / `clear_futures_daily` 不存在）

- [ ] **Step 3: 实现** — `futures_data.py`：

替换 `contract_series`（原 45-61 行）：

```python
def _third_friday(ym: str) -> str:
    """'2608' → 该月第三个周五 'YYYY-MM-DD'（CFFEX 到期日兜底计算）。"""
    import datetime

    y, m = 2000 + int(ym[:2]), int(ym[2:4])
    first = datetime.date(y, m, 1)
    fri = first + datetime.timedelta(days=(4 - first.weekday()) % 7)
    return (fri + datetime.timedelta(days=14)).isoformat()


def contract_series(client: TushareClient) -> dict[str, list[tuple[str, str]]]:
    """fut_basic 元数据 → {IF: [(IF1504.CFX, '2015-04-17'), ...]}（按代码升序）。

    到期日取 last_trade_date；缺失退 last_ddate（金融期货最后交易日=最后交割日）；
    再缺失按 CFFEX 规则兜底计算该合约月第三个周五。当月合约序列据此划分
    窗口：前合约到期日 < date <= 本合约到期日（相邻合约重叠日按到期日
    边界划分，不再按月划分——月内到期日→月末的交易日归下一合约）。
    """
    df = client.query("fut_basic", exchange="CFFEX")
    if df is None or df.empty:
        raise RuntimeError("fut_basic(CFFEX) 无数据")
    series: dict[str, set[tuple[str, str]]] = {}
    for _, r in df.iterrows():
        code = str(r.get("ts_code", ""))
        if len(code) != 10 or code[6:] != ".CFX" or code[:2] not in INDEX_MAP:
            continue
        lt = str(r.get("last_trade_date") or r.get("last_ddate") or "")
        if len(lt) == 8:
            expiry = f"{lt[:4]}-{lt[4:6]}-{lt[6:8]}"
        else:
            expiry = _third_friday(code[2:6])
        series.setdefault(code[:2], set()).add((code, expiry))
    return {sym: sorted(codes) for sym, codes in series.items()}
```

替换 `fetch_contract` 签名与过滤（原 64-89 行，过滤行 74-75 改为窗口判断）：

```python
def fetch_contract(
    client: TushareClient, contract: str,
    window_start: str, window_end: str,
) -> list[dict]:
    """单合约 fut_daily → 当月窗口内 rows（window_start < date <= window_end）。

    window_end = 本合约到期日（last_trade_date）；window_start = 前一合约到期日
    （序列首合约传 start_month 月初前一日，使月初含入）。当月口径唯一性由
    到期日边界保证：相邻合约重叠交易日的行按边界划分，每月 40% 交易日
    不再丢失（原按月过滤：到期日→月末的行属于下月合约却未被下月窗口收留）。
    """
    df = client.query("fut_daily", ts_code=contract)
    rows: list[dict] = []
    for _, r in df.iterrows():
        d = str(r.get("trade_date", ""))
        settle = safe_float(r.get("settle"))
        if len(d) != 8 or settle is None:
            continue
        iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        if not (window_start < iso <= window_end):
            continue
        rows.append({
            "date": iso,
            "symbol": contract[:2],
            "contract": contract,
            "open": safe_float(r.get("open")),
            "high": safe_float(r.get("high")),
            "low": safe_float(r.get("low")),
            "close": safe_float(r.get("close")),
            "settle": settle,
            "oi": safe_float(r.get("oi")),
            "oi_chg": safe_float(r.get("oi_chg")),
            "source": "tushare",
        })
    return rows
```

替换 `ensure_futures_daily` 的签名、existing 初始化与合约循环（原 163-196 行）：

```python
def ensure_futures_daily(
    start_month: str = "2015-04", max_contracts: int = 200, *, force: bool = False,
) -> dict:
    """回填/增量：已入库合约跳过（断点续跑）。返回 {fetched, failed, skipped}。

    force=True：先清空 futures_daily 再全量重建（finding #1 数据修复用——
    旧表按月划分含每月 40% 交易日洞，必须重建）。

    Tushare 主源失败 → sina 降级（fill-only：仅补缺失日期，绝不覆盖已有行，
    source='sina' 标注——merge COALESCE 逐列覆盖会把 close 口径的基差写进
    settle 口径的 tushare 行，杂交口径必须禁止）。
    """
    import datetime

    if force:
        store.clear_futures_daily()
        existing: set[str] = set()
    else:
        existing = store.futures_contracts()
    # start_month "2015-04" → "1504"（与合约代码月份段同格式，字符串可比）
    start_ym = start_month.replace("-", "")[2:]
    first_start = (datetime.date.fromisoformat(f"{start_month}-01")
                   - datetime.timedelta(days=1)).isoformat()
    try:
        client = _make_client()
        series = contract_series(client)
        index_closes = fetch_index_close_map()
        fetched: list[str] = []
        failed: dict[str, str] = {}
        for sym, contracts in series.items():
            prev_lt = first_start  # 首合约窗口起点 = 起始月月初前一日（月初含入）
            for contract, expiry in contracts:
                if contract[2:6] < start_ym:
                    continue
                if contract in existing:
                    prev_lt = expiry
                    continue
                if len(fetched) >= max_contracts:
                    break
                try:
                    rows = fetch_contract(client, contract, prev_lt, expiry)
                    rows = compute_basis(rows, index_closes.get(sym, {}))
                    if rows:
                        store.save_futures_daily(rows)
                        fetched.append(contract)
                except Exception as exc:  # noqa: BLE001 — 逐合约容错
                    failed[contract] = str(exc)
                    logger.warning("futures fetch failed %s: %s", contract, exc)
                prev_lt = expiry
        return {"fetched": fetched, "failed": failed, "skipped": len(existing),
                "source": "tushare"}
    except Exception as exc:  # noqa: BLE001 — 主源整体失败 → sina 降级
        # ...（sina 降级路径保持不变，原 198-224 行原样保留）
```

`store.py` 在 `save_futures_daily` 之后追加：

```python
def clear_futures_daily() -> int:
    """清空 futures_daily（--force 全量重建用）。返回删除行数。"""
    init_db()
    c = _conn()
    try:
        n = c.execute("DELETE FROM futures_daily").rowcount
        c.commit()
        return n
    finally:
        _safe_close(c)
```

`scripts/backfill_futures_daily.py` 增加参数（原 27-33 行）：

```python
    parser = argparse.ArgumentParser(description="futures_daily 股指期货回填")
    parser.add_argument("--start", default="2015-04", help="起始月 YYYY-MM")
    parser.add_argument("--max", dest="max_contracts", type=int, default=600)
    parser.add_argument("--force", action="store_true",
                        help="清空 futures_daily 后全量重建（数据口径修复用）")
    args = parser.parse_args()

    store.init_db()
    result = ensure_futures_daily(start_month=args.start, max_contracts=args.max_contracts,
                                  force=args.force)
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run python -m pytest skills/invest-a-stock/tests/test_futures_data.py -q`
Expected: PASS 全绿

- [ ] **Step 5: Commit**

```bash
git add skills/invest-a-stock/scripts/lib/futures_data.py skills/invest-a-stock/scripts/lib/store.py scripts/backfill_futures_daily.py skills/invest-a-stock/tests/test_futures_data.py
git commit -m "fix: futures_daily 按到期日划分当月窗口（修复每月 40% 交易日缺失）+ --force 重建"
```

---

### Task 3: E 系列事件首日语义统一（finding #3/#4/#5）

**Files:**
- Modify: `scripts/scenario_baselines.py`（trigger_flags + touch_within 提取 + main 调用）
- Test: `skills/lib/tests/test_scenario_triggers.py`（追加 5 用例）

- [ ] **Step 1: 写失败测试** — 追加到 `test_scenario_triggers.py` 的 `TestTriggers`：

```python
    def test_close_below_no_phantom_at_series_start(self):
        # 回归（finding #4）：序列起点即处于跌破状态 → 首行不得计"跌破首日"
        # below=[T,T,F,F,T]；起点前状态视为已存在 → idx0 抑制；idx4 再入计事件
        df = _frame([98.0, 97.0, 99.0, 101.0, 98.5])
        flags = sb.trigger_flags(df, {"kind": "close_below", "level": 99.0})
        assert flags.tolist() == [False, False, False, False, True]

    def test_close_near_once_per_state(self):
        # 回归（finding #3）：连续在带内 → 仅段首日计事件
        # near=[F,T,T,F,T] → 去重后 [F,T,F,F,T]
        df = _frame([4000.0, 3960.0, 3959.0, 4000.0, 3960.0])
        flags = sb.trigger_flags(df, {"kind": "close_near", "level": 3960.26, "tol_pct": 0.3})
        assert flags.tolist() == [False, True, False, False, True]

    def test_boll_position_once_per_state(self):
        # 回归（finding #3）：急涨段连续 4 日 pos>=95 → 仅段首日计事件
        df = _frame([100.0] * 20 + [130.0] * 5)
        flags = sb.trigger_flags(df, {"kind": "boll_position", "level": 95.0})
        assert flags.sum() == 1
        assert flags.tolist().index(True) == 20

    def test_touch_window_includes_day_60(self):
        # 回归（finding #5）：第 60 日（i+60）触达目标位必须计入（旧切片漏第 60 日）
        closes = [100.0] * 80
        closes[10] = 90.0   # 事件日 i=10
        closes[70] = 50.0   # i+60 触达目标 60
        r = sb.touch_within(closes, [10], [60.0])
        assert r == {"n": 1, "ratio": 1.0}

    def test_touch_truncated_window_excluded(self):
        # 窗口被序列末尾截断（不足 60 日）→ 事件不入分母（对齐 lmw truncated 语义）
        r = sb.touch_within([100.0] * 30, [10], [60.0])
        assert r is None
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest skills/lib/tests/test_scenario_triggers.py -q`
Expected: FAIL（幻影/去重/窗口/`touch_within` 不存在）

- [ ] **Step 3: 实现** — 替换 `trigger_flags`（原 48-70 行）：

```python
def trigger_flags(df: pd.DataFrame, spec: dict) -> pd.Series:
    """触发日布尔序列（按 kind 分派）。

    事件日 = 状态段首日（每段仅计一次，避免 forward 窗口重叠自相关）；
    数据起点前状态视为已存在（fill_value=True）——序列起点即处于某状态
    时首行不计事件（1990-12-19 起即低于各触发位 → 不产生"跌破首日"幻影）。
    """
    closes = df["close"].astype(float)
    kind = spec["kind"]
    level = spec["level"]
    if kind == "close_below":
        below = closes < level
        return below & ~below.shift(1, fill_value=True)
    if kind == "close_above_3d":
        # 连续 3 日站稳，事件日 = 第 3 日（rolling 首两行 NaN 天然保护起点）
        above3 = (closes > level).rolling(3).sum() == 3
        return above3 & ~above3.shift(1, fill_value=False)
    if kind == "close_near":
        near = (closes - level).abs() / level * 100 <= spec["tol_pct"]
        return near & ~near.shift(1, fill_value=True)
    if kind == "boll_position":
        mid = closes.rolling(20).mean()
        std = closes.rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        pos = (closes - lower) / (upper - lower) * 100
        hi = pos >= level
        return hi & ~hi.shift(1, fill_value=True)
    raise ValueError(f"未知触发类型: {kind}")
```

在 `trigger_flags` 之后追加 `touch_within`，并将 `main()` 中 E-004 块（原 116-128 行）替换为调用：

```python
def touch_within(
    closes: list[float], hit_idx: list[int], targets: list[float], *,
    horizon: int = 60, n: int | None = None,
) -> dict | None:
    """事件后 horizon 日内（i+1..i+horizon，含第 horizon 日）触及任一目标位的
    事件占比。窗口不足 horizon 日（被序列末尾截断）的事件不入分母
    （对齐 lmw truncated 语义：不完整窗口不判"未触及"）。
    无完整窗口事件 → None。"""
    n = len(closes) if n is None else n
    touched = complete = 0
    for i in hit_idx:
        if i + horizon + 1 > n:
            continue
        window = closes[i + 1 : i + horizon + 1]
        complete += 1
        if any(c <= t for c in window for t in targets):
            touched += 1
    if not complete:
        return None
    return {"n": touched, "ratio": round(touched / complete, 4)}
```

`main()` 中（原 116-128 行）：

```python
        if spec.get("track"):
            # E-004：跌破后 60 日内（含第 60 日）触及任一五浪目标位的比例
            entry["touch_wave_target_60d"] = touch_within(closes, hit_idx, spec["track"])
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run python -m pytest skills/lib/tests/test_scenario_triggers.py -q`
Expected: PASS 全绿（既有 8 用例 + 新 5 用例；`test_close_below`/`test_close_near`/`test_boll_position` 既有断言与新语义兼容——已逐向量验证）

- [ ] **Step 5: Commit**

```bash
git add scripts/scenario_baselines.py skills/lib/tests/test_scenario_triggers.py
git commit -m "fix: E 系列事件首日语义统一（幻影首行/重叠窗口/60 日 off-by-one）+ 测试"
```

---

### Task 4: H6 缺口扫描 g≥1 钳制（finding #6）

**Files:**
- Modify: `scripts/backtest_h6.py:109`
- Test: `skills/lib/tests/test_h6_events.py`（追加 1 用例）

- [ ] **Step 1: 写失败测试** — 追加到 `TestDetectEvents`：

```python
    def test_no_phantom_gap_at_series_start(self):
        """回归（finding #6）：i=60 扫描 g 不得下探到 0（highs[-1] 是未来数据）。

        高位平台后崩盘：lows[0]=99.6 > highs[-1]=50.4、lows[60]=49.6 <= 50.4、
        全程无真实向上缺口。旧实现以 highs[-1]（末日高点）为下沿在 i=60
        伪造 gap 事件（已实测复现 [(60, 0, 50.4)]）；修复后必须 0 事件。
        """
        closes = [100.0] * 60 + [50.0] * 20
        highs = [c + 0.4 for c in closes]
        lows = [c - 0.4 for c in closes]
        df = _frame(closes, highs, lows)
        df["adx14"] = 30.0
        df["ma20"] = float("nan")
        df["boll_lower"] = float("nan")
        gap_events = [e for e in h6.detect_events(df) if e["type"] == "gap"]
        assert gap_events == []
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest skills/lib/tests/test_h6_events.py::TestDetectEvents::test_no_phantom_gap_at_series_start -q`
Expected: FAIL（`assert [] == [(60, ...)]` 类——幽灵事件存在）

- [ ] **Step 3: 实现** — `backtest_h6.py:109` 单行：

```python
        for g in range(i - 2, max(1, i - GAP_LOOKBACK) - 1, -1):
```

（`max(0, ...)` → `max(1, ...)`；g≥1 后 `highs[g-1]` 不再负索引读序列末行）

- [ ] **Step 4: 运行确认通过**

Run: `uv run python -m pytest skills/lib/tests/test_h6_events.py -q`
Expected: PASS 全绿

- [ ] **Step 5: Commit**

```bash
git add scripts/backtest_h6.py skills/lib/tests/test_h6_events.py
git commit -m "fix: H6 缺口扫描 g≥1 钳制（highs[-1] 未来数据泄漏）+ 测试"
```

---

### Task 5: F1/F2 expanding 分位 + F2 守卫 + F3 日历对齐（finding #7/#8 + look-ahead）

**Files:**
- Modify: `skills/lib/stats.py`（新增 expanding_percentile_rank）
- Modify: `scripts/backtest_futures.py`（F1/F2 分位、F2 守卫、F3 重构）
- Test: `skills/lib/tests/test_stats.py`（追加类）；新建 `skills/lib/tests/test_backtest_futures_fixes.py`

- [ ] **Step 1: 写失败测试**

`test_stats.py` 追加：

```python
from stats import expanding_percentile_rank, percentile_rank_inclusive  # 顶部 import 更新


class TestExpandingPercentileRank:
    def test_no_lookahead(self):
        vals = [10.0, 5.0, 8.0, 6.0, 4.0]
        got = expanding_percentile_rank(vals)
        assert got == [100.0, 50.0, 66.66666666666666, 50.0, 20.0]

    def test_differs_from_full_series(self):
        # 全序列分位（含未来）与 expanding 分位不同——look-ahead 修复的判别点
        vals = [10.0, 5.0, 8.0, 6.0, 4.0]
        full = [percentile_rank_inclusive(vals, v) for v in vals]
        assert full == [100.0, 40.0, 80.0, 60.0, 20.0]
        assert expanding_percentile_rank(vals) != full

    def test_none_and_nan_passthrough(self):
        got = expanding_percentile_rank([1.0, None, 2.0])
        assert got[1] is None
        assert got[2] == 100.0
```

新建 `skills/lib/tests/test_backtest_futures_fixes.py`：

```python
"""backtest_futures 修复回归测试 — fixture，不联网。

覆盖（finding #7/#8 + look-ahead）：F2 日期守卫（KeyError 修复）、
F3 日历对齐 helper（基差/收益共用指数交易日历）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "skills"), str(_REPO_ROOT / "skills" / "lib"),
           str(_REPO_ROOT / "skills" / "invest-a-stock" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location(
    "backtest_futures", _REPO_ROOT / "scripts" / "backtest_futures.py")
bt = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(bt)


class TestBasisStateAfter:
    def test_converge_and_uptick(self):
        dates = [f"2026-01-{i + 1:02d}" for i in range(30)]
        closes = {d: 100.0 + i for i, d in enumerate(dates)}
        all_dates = sorted(closes)
        basis_map = {"2026-01-05": -10.0, "2026-01-25": -5.0}
        st = bt._basis_state_after(basis_map, closes, all_dates, "2026-01-05")
        assert st[0] == "converge"
        assert st[1] == pytest.approx(19.2308, abs=1e-4)

    def test_target_date_missing_in_futures_calendar(self):
        closes = {d: 100.0 for d in [f"2026-01-{i + 1:02d}" for i in range(30)]}
        all_dates = sorted(closes)
        basis_map = {"2026-01-05": -10.0}  # 目标日 01-25 不在期货日历
        assert bt._basis_state_after(basis_map, closes, all_dates, "2026-01-05") is None

    def test_event_date_outside_index_calendar(self):
        closes = {d: 100.0 for d in [f"2026-01-{i + 1:02d}" for i in range(30)]}
        all_dates = sorted(closes)
        assert bt._basis_state_after({}, closes, all_dates, "2025-12-31") is None

    def test_horizon_beyond_end(self):
        closes = {d: 100.0 for d in [f"2026-01-{i + 1:02d}" for i in range(10)]}
        all_dates = sorted(closes)
        assert bt._basis_state_after({}, closes, all_dates, "2026-01-05") is None


class TestRunF2Guard:
    def test_futures_date_missing_from_index_calendar_skipped(self, monkeypatch, tmp_path):
        """回归（finding #8）：事件日不在指数收盘日历 → 跳过而非 KeyError。"""
        n = 120
        dates = [f"2026-01-{i + 1:02d}" for i in range(n)]
        basis = [0.5] * n
        basis[60] = -5.0  # expanding 分位最低 → 唯一 deep_discount 事件日
        fdf = pd.DataFrame({"date": dates, "basis_pct": basis})
        monkeypatch.setattr(bt, "load_futures_df", lambda sym: fdf)
        idx_closes = {d: 100.0 + i for i, d in enumerate(dates) if d != dates[60]}
        monkeypatch.setattr(bt, "load_index_closes", lambda code: idx_closes)
        out = tmp_path / "f2.json"
        bt.run_f2(out)  # 修复前在此抛 KeyError
        assert out.exists()
        res = json.loads(out.read_text(encoding="utf-8"))
        entry = res["scenarios"]["IF"]
        assert entry["n_events"]["deep_discount"] == 1
        assert "+5" not in entry["deep_discount"]  # 唯一事件日被跳过 → 无 forward 统计
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest skills/lib/tests/test_stats.py::TestExpandingPercentileRank skills/lib/tests/test_backtest_futures_fixes.py -q`
Expected: FAIL（`expanding_percentile_rank` 不存在；`_basis_state_after` 不存在；run_f2 抛 `KeyError`）

- [ ] **Step 3: 实现**

`stats.py` 在 `percentile_rank_mid` 之后追加：

```python
def expanding_percentile_rank(seq: list[float]) -> list[float | None]:
    """截至当日的历史分位（expanding window）——每个元素的分位只依赖它
    自己及之前的序列，杜绝事件标签中的未来信息泄漏（look-ahead）。
    None/NaN 元素输出 None（NaN 不参与分母，与含边界分位语义一致）。"""
    out: list[float | None] = []
    seen: list[float] = []
    for v in seq:
        if v is None or (isinstance(v, float) and v != v):
            out.append(None)
        else:
            seen.append(v)
            out.append(percentile_rank_inclusive(seen, v))
    return out
```

`backtest_futures.py`：

① 顶部 import（原 34 行）：

```python
from stats import expanding_percentile_rank  # noqa: E402
```

（删除 `percentile_rank_inclusive` import——改造后无直接调用）

② `run_f1` 分位块（原 82-87 行）：

```python
        # 基差历史分位：expanding window（截至当日——全序列分位把未来
        # 信息泄漏进当日标签，look-ahead 修复）
        basis = fdf["basis_pct"].astype(float)
        fdf["depth_pctile"] = expanding_percentile_rank(basis.tolist())
        fdf["quartile"] = pd.cut(fdf["depth_pctile"], [0, 25, 50, 75, 100],
                                 labels=["Q1_深贴水", "Q2", "Q3", "Q4_浅贴水升水"], include_lowest=True)
```

③ `run_f2` 分位块（原 149-150 行）：

```python
        basis = fdf["basis_pct"].astype(float).tolist()
        # expanding 分位（无未来信息；升序分位：p<10 深贴水 / p>90 升水）
        fdf["pctile"] = expanding_percentile_rank(basis)
```

④ `run_f2` 事件循环守卫（原 165 行 `for d in ev_dates:` 后）：

```python
            for d in ev_dates:
                if d not in closes:
                    continue  # 期货交易日不在指数收盘日历（补班/数据缺口）→ 跳过
                keys = [k for k in all_dates if k > d]
```

⑤ `run_f3` 之前追加 helper（放在 `run_f2` 与 `run_f3` 之间）：

```python
def _basis_state_after(
    basis_map: dict[str, float], closes: dict[str, float],
    all_dates: list[str], d: str, *, horizon: int = 20,
) -> tuple[str, float] | None:
    """事件日 d 后 horizon 个指数交易日的（基差方向, 指数收益%）。

    基差与收益共用指数交易日历（原实现期货行号 fi+20 vs 指数行号 idx0+20
    错位，且每月数据洞放大错位——finding #7）；
    事件日不在指数日历 / 目标日不在期货日历 / 任一端基差缺失 → None。
    """
    idx0 = all_dates.index(d) if d in all_dates else None
    if idx0 is None or idx0 + horizon >= len(all_dates):
        return None
    tgt = all_dates[idx0 + horizon]
    b0 = basis_map.get(d)
    b1 = basis_map.get(tgt)
    if b0 is None or b1 is None or pd.isna(b0) or pd.isna(b1):
        return None
    basis_dir = "converge" if abs(b1) < abs(b0) else "diverge"
    idx_chg = (closes[tgt] / closes[d] - 1) * 100
    return basis_dir, idx_chg
```

⑥ `run_f3` 的 OI 块（原 194-202 行）替换为：

```python
        # 20 日持仓变化：共享 helper（口径与消费方一致——掩码/有效数阈值
        # 单份实现；finding #10）。定位为展期节奏度量（F3 结论已降级为
        # "不可刻画持仓状态"，此处仅保留历史演变刻画用）
        oi_list = fdf["oi_change_pct"].tolist()
        fdf["oi_20d_chg"] = [
            compound_oi_change(oi_list[max(0, i - 19) : i + 1]) for i in range(len(oi_list))
        ]
```

（文件顶部追加 `from lib.futures_data import compound_oi_change  # noqa: E402`——放在 `load_futures_df` 内 import 之后、模块级；注意 `load_futures_df` 内已有 `from lib import store` 惰性导入模式，此处模块级 import 在 sys.path 已含 `skills/invest-a-stock/scripts` 时安全）

⑦ `run_f3` 事件循环（原 206-237 行）替换为：

```python
        entry = {"up": {}, "down": {}, "n_events": {}}
        basis_map = dict(zip(fdf["date"], fdf["basis_pct"].astype(float)))
        for state, cond in (("up", lambda v: v >= 5.0), ("down", lambda v: v <= -5.0)):
            ev_dates = []
            prev_state = False
            for _, row in fdf.iterrows():
                cur = cond(row["oi_20d_chg"]) if row["oi_20d_chg"] is not None else False
                if cur and not prev_state:
                    ev_dates.append(row["date"])
                prev_state = cur
            cross: dict[tuple[str, str], int] = defaultdict(int)
            for d in ev_dates:
                st = _basis_state_after(basis_map, closes, all_dates, d)
                if st is None:
                    continue
                basis_dir, idx_chg = st
                idx_dir = "up" if idx_chg > 0 else "down"
                cross[(basis_dir, idx_dir)] += 1
            entry[state] = {"cross_tab": {f"{k[0]}|{k[1]}": v for k, v in sorted(cross.items())},
                            "n": len(ev_dates)}
            entry["n_events"][state] = len(ev_dates)
```

（同时删除原 `closes = load_index_closes(idx_code)` 之后遗留的 `idx0`/`fut_dates`/`fi` 死守卫——新循环不再使用；`closes`/`all_dates` 定义保留在 `run_f3` 顶部不变）

- [ ] **Step 4: 运行确认通过**

Run: `uv run python -m pytest skills/lib/tests/test_stats.py skills/lib/tests/test_backtest_futures_fixes.py -q`
Expected: PASS 全绿

- [ ] **Step 5: Commit**

```bash
git add skills/lib/stats.py scripts/backtest_futures.py skills/lib/tests/test_stats.py skills/lib/tests/test_backtest_futures_fixes.py
git commit -m "fix: F1/F2 expanding 分位去 look-ahead + F2 日期守卫 + F3 日历对齐口径统一"
```

---

### Task 6: OI 20 日变化从用户标签移除（finding #2 + 决策）

**Files:**
- Modify: `skills/invest-a-journal/scripts/lib/market_microstructure.py`（_fetch_futures + 两处 label）
- Modify: `skills/invest-a-etf/scripts/lib/futures_basis.py`（删 OI 块 + docstring）
- Modify: `skills/lib/data_bridge.py:274`（docstring 注记）
- Modify: `skills/invest-a-pulse/SKILL.md:206`、`skills/invest-a-etf/references/report-template.md:173`（文案）
- Test: `skills/invest-a-etf/tests/test_futures_basis.py`、`skills/invest-a-journal/tests/test_labels_v2.py`（各追加 1 用例）

- [ ] **Step 1: 写失败测试**

`test_futures_basis.py` 的 `TestQuery` 追加：

```python
    def test_no_oi_field_in_output(self, monkeypatch):
        """回归（finding #2）：OI 20 日变化已从用户输出移除。"""
        import lib.store as store_mod  # noqa: E402

        monkeypatch.setattr(store_mod, "load_futures_daily", lambda symbol=None, limit=1000: _fake_rows(100))
        r = query_futures_basis("510500")
        assert r["available"] is True
        assert "oi_20d_chg_pct" not in r
```

`test_labels_v2.py` 追加：

```python
class TestCapitalFlowNoOiSegment:
    def test_capital_flow_has_no_oi_segment(self):
        """回归（finding #2）：资金面标签不再输出 OI 20 日变化段（F3 已裁定
        该口径为展期节奏主导、不可刻画持仓状态）。"""
        from market_microstructure import _compute_labels_v2

        snap = {"date": "20260807", "futures_basis_pct": -8.5, "futures_oi_change_pct": -74.5}
        _compute_labels_v2(snap, [])
        assert "持仓" not in snap["label_capital_flow"]
        assert "基差 -8.50%" in snap["label_capital_flow"]
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest skills/invest-a-etf/tests/test_futures_basis.py::TestQuery::test_no_oi_field_in_output skills/invest-a-journal/tests/test_labels_v2.py::TestCapitalFlowNoOiSegment -q`
Expected: FAIL（`oi_20d_chg_pct` 键仍存在；label 含"持仓"段）

- [ ] **Step 3: 实现**

`market_microstructure.py`：

① `_fetch_futures`（原 923-953 行）替换为：

```python
def _fetch_futures(result: dict) -> None:
    """股指期货基差（v0.2.6 F 系列）——机构对冲成本状态度量，非预测。

    读 futures_daily 最新一行（IC 为主品种，代表中小盘对冲成本）。
    注：持仓量 20 日变化已由 F3 实证裁定为展期节奏主导（不可刻画持仓
    状态），本修订起不再输出到用户标签；字段与 compound_oi_change
    helper 保留于数据层供后续研究。
    """
    try:
        from lib import store as _store  # noqa: E402 — 惰性导入
        _store.init_db()
        rows = _store.load_futures_daily(symbol="IC", limit=21)
        if not rows:
            return
        latest = rows[-1]
        if latest.get("basis_pct") is not None:
            result["futures_basis_pct"] = latest["basis_pct"]
    except Exception:  # noqa: BLE001 — 单维度失败不阻塞
        result["_errors"].append("futures 读取失败（降级：资金面缺期货维度）")
```

② `_compute_labels_v2` 资金面段（原 504-510 行）替换为：

```python
    basis = snap.get("futures_basis_pct")
    if basis is not None:
        parts.append(f"IC 基差 {basis:+.2f}%")
    snap["label_capital_flow"] = "；".join(parts)
```

③ `_compute_labels` 兼容层资金面段（原 614-619 行）替换为：

```python
    basis = result.get("futures_basis_pct")
    if basis is not None:
        parts.append(f"IC 基差 {basis:+.2f}%")
    if parts:
        result["label_capital_flow"] = "；".join(parts)
```

`futures_basis.py`：

④ docstring 第 5-6 行改为（删除"持仓量 20 日变化 + "）：

```python
    输出 = 该 ETF 对应股指期货品种的当前基差水平 + 历史分位（伴随中位数，
    估值分位规则同款）+ 历史演变分布参照（条件句，非必然）。
```

⑤ 删除 OI 块（原 84-96 行整块，含注释）：

```python
    # 持仓量 20 日变化：...（整块删除——F3 已裁定该口径为展期节奏主导，
    # 不可刻画持仓状态；finding #2 用户决策：从用户输出移除）
```

`data_bridge.py`：

⑥ docstring（原 274-276 行）改为：

```python
    返回 {IF|IH|IC|IM: {date, contract, basis_pct, oi_change_pct, source}}
    最新一日的四品种状态 | None（不可得）。依赖 invest-a-stock 的 store/futures_data。
    注：oi_change_pct 为单日日环比（非 20 日复利变化）；20 日口径见
    futures_data.compound_oi_change（v0.2.6 起 20 日口径已从用户标签移除）。
```

`skills/invest-a-pulse/SKILL.md` ⑦（原 206 行整行替换）：

```
- IC 股指期货基差 {futures_basis_pct}%（负 = 贴水 = 对冲成本高）——机构对冲盘行为状态（v0.2.6 F 系列；状态度量，非预测；分红期基差假收窄口径注记）
```

`skills/invest-a-etf/references/report-template.md` ⑧（删除原 173 行整行）：

```
| 持仓量 20 日变化 | ±X% | futures_daily 字段 |
```

- [ ] **Step 4: 残留引用清零检查**

Run: `grep -rn "futures_oi_change_pct\|oi_20d_chg_pct\|持仓量 20 日变化\|持仓 20 日" skills/ --include="*.py" --include="*.md" | grep -v "backtest_prereg\|test_\|docs/data"`
Expected: 仅剩 `docs/data/*.json` 与 `backtest_prereg/F3_预注册.md`（预注册为冻结历史记录，不改）；`scripts/backtest_futures.py` 的 F3 内部注释可保留（非用户标签）。如有意外残留（如 pulse.py / journal.py 注入点）→ 同步删除。

- [ ] **Step 5: 运行确认通过**

Run: `uv run python -m pytest skills/invest-a-etf/tests/test_futures_basis.py skills/invest-a-journal/tests/test_labels_v2.py -q`
Expected: PASS（既有用例 + 新用例全绿；若 `test_microstructure_via_bridge.py` 断言了 `futures_oi_change_pct` 字段则更新该断言为基差字段存在）

- [ ] **Step 6: Commit**

```bash
git add skills/invest-a-journal/scripts/lib/market_microstructure.py skills/invest-a-etf/scripts/lib/futures_basis.py skills/lib/data_bridge.py skills/invest-a-pulse/SKILL.md skills/invest-a-etf/references/report-template.md skills/invest-a-etf/tests/test_futures_basis.py skills/invest-a-journal/tests/test_labels_v2.py
git commit -m "fix: OI 20 日变化从用户标签移除（journal/ETF/pulse，与 F3 裁定一致）+ 测试"
```

---

### Task 7: ADX 独立手算 golden oracle（finding #9）

**Files:**
- Test: `skills/lib/tests/test_technical_adx.py`（追加固件类；镜像测试 docstring 降级）

- [ ] **Step 1: 写失败测试** — 文件顶部 docstring（原 1-6 行）改为：

```python
"""technical.adx 回归测试 — Wilder 平均口径（累计均值种子 + 平均递推）。

背景（2026-08-15 /code-review #10）：_wilder 曾用「前 n 项和」种子配合
平均形式递推（v/n），早期条数被放大 ~n 倍后缓慢衰减，趋势反转序列上
前 ~30-50 根 ADX 最多偏高 ~53 点。镜像测试锁定该口径、防止回退
（约定锁定，非独立 oracle）；真 oracle 见 TestAdxGoldenFixture——
期望值由独立手算推导、硬编码，不经过生产实现任何代码路径。
"""
```

`_adx_ref` 的 docstring（原 34 行）改为：

```python
    """镜像实现（约定锁定用）：与生产逐行同构，仅防 seed/递推回退。

    注意：同构代码不构成独立 oracle——共享的系统性错误两侧一致、
    测试照绿；独立校验见 TestAdxGoldenFixture。
    """
```

追加（`_uptrend_then_reverse` 之后）：

```python
# 独立手算固件（n=3 短窗，推导不经过生产实现）：
# closes=[100,101,100.5,102,101,100,99.5,98,99,97]
# highs =[100.8,101.6,101.4,102.6,101.8,100.9,100.2,99.2,99.8,98.4]
# lows  =[99.6,100.4,100.0,101.2,100.4,99.2,98.8,97.2,98.0,96.4]
# TR=[0,1.6,1.4,2.1,1.6,1.8,1.4,2.3,1.8,2.6]
# +DM=[0,.8,0,1.2,0,0,0,0,.6,0]  −DM=[0,0,.4,0,.8,1.2,.4,1.6,0,1.6]
# Wilder(n=3)（累计均值种子 + prev*2/3+v/3）→ DX@idx:
# {1:100, 2:33.33, 3:73.33, 4:8.33, 5:41.24, 6:52.18, 7:77.42, 8:36.81, 9:67.01}
# ADX 再平滑（同口径），2n−1=5 起发布：
GOLDEN_FIXTURE = {
    "closes": [100.0, 101.0, 100.5, 102.0, 101.0, 100.0, 99.5, 98.0, 99.0, 97.0],
    "highs": [100.8, 101.6, 101.4, 102.6, 101.8, 100.9, 100.2, 99.2, 99.8, 98.4],
    "lows": [99.6, 100.4, 100.0, 101.2, 100.4, 99.2, 98.8, 97.2, 98.0, 96.4],
    "expected": [None, None, None, None, None, 46.22, 48.21, 57.94, 50.9, 56.27],
}


class TestAdxGoldenFixture:
    def test_golden_values(self):
        """独立手算固件逐条精确匹配——真 oracle（非镜像）。"""
        h = GOLDEN_FIXTURE["highs"]
        l = GOLDEN_FIXTURE["lows"]
        c = GOLDEN_FIXTURE["closes"]
        got = adx(h, l, c, n=3)
        assert got == GOLDEN_FIXTURE["expected"]

    def test_golden_steady_trend(self):
        """纯单边趋势 n=3：+DM/TR 恒比 → DX=100 → 5 位起全 100。"""
        closes = [100.0 + 0.5 * i for i in range(10)]
        got = adx([c + 0.4 for c in closes], [c - 0.4 for c in closes], closes, n=3)
        assert got[5:] == [100.0] * 5
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python -m pytest skills/lib/tests/test_technical_adx.py::TestAdxGoldenFixture -q`
Expected: 新类直接 PASS（生产实现已与独立推导一致——本任务性质为"补独立 oracle"；镜像测试重命名为约定锁定）。若 FAIL 则说明生产实现与独立推导分歧，停下来报告（不要改期望值迁就生产）。

- [ ] **Step 3: 运行确认通过**

Run: `uv run python -m pytest skills/lib/tests/test_technical_adx.py -q`
Expected: PASS 全绿

- [ ] **Step 4: Commit**

```bash
git add skills/lib/tests/test_technical_adx.py
git commit -m "fix: ADX 独立手算 golden oracle（镜像测试降级为约定锁定）"
```

---

### Task 8: futures_daily 全量重建 + 完整性验证（finding #1 数据落库）

**Files:** 无代码改动（DB 操作 + 验证）

- [ ] **Step 1: --force 回填**

Run: `uv run python scripts/backfill_futures_daily.py --force`
Expected: JSON 输出 `{"fetched": [~460 合约], "failed": {...}, "skipped": 0, "source": "tushare"}`，耗时约 10 分钟（tushare 80/min 限速）。若 `failed` 含大量 "call limit" 类错误（日调用上限），**不要再次 --force**，改为：

```bash
uv run python scripts/backfill_futures_daily.py
```

（无 --force 断点续跑，已入库合约跳过）

- [ ] **Step 2: 完整性验证（Python，必须跑）**

Run:

```bash
uv run python - <<'EOF'
import sys, datetime
from collections import defaultdict
sys.path.insert(0, "skills"); sys.path.insert(0, "skills/invest-a-stock/scripts")
from lib import store

rows = store.load_futures_daily(limit=100000)
bym, byd = defaultdict(int), defaultdict(set)
for r in rows:
    bym[(r["symbol"], r["date"][:7])] += 1
    byd[r["symbol"]].add(r["date"])
print("total rows:", len(rows))
for sym in ("IF", "IH", "IC", "IM"):
    cnts = sorted(v for (s, m), v in bym.items() if s == sym)
    if not cnts:
        print(sym, "EMPTY"); continue
    ds = sorted(byd[sym])
    gaps = [1 for a, b in zip(ds, ds[1:])
            if (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days > 3]
    print(f"{sym}: months={len(cnts)} median_rows/month={cnts[len(cnts)//2]} "
          f"dates={len(ds)} gaps>3d={len(gaps)} range={ds[0]}..{ds[-1]}")
EOF
```

Expected: 各品种 `median_rows/month >= 20`（完整月 21-23）且 `gaps>3d == 0`（修复前 IC 有 161 个 >3 日缺口）；IM 自 2022-07 起。若不满足 → 停，查 `fetch_contract` 窗口逻辑。

- [ ] **Step 3: Commit（数据快照变更）**

```bash
git add -A && git status  # 检查是否有非预期变更
```

（futures_daily 为本地 sqlite，不随 git；此步仅确认无意外文件变化。DB 文件不在版本库则跳过提交，直接进入 Task 9）

---

### Task 9: 重跑 F1/F2/F3 + 报告/文档数字同步

**Files:**
- Regenerate: `docs/data/F1_backtest_result.json`、`F2_backtest_result.json`、`F3_backtest_result.json`
- Modify: `host-docs/v0.2.6/F系列期货状态刻画报告_20260815.md`、`host-docs/v0.2.6/股指期货数据融入ETF分析_调研方案_20260814.md`（146-148 行）、`CHANGELOG.md`（F 系列段落）

- [ ] **Step 1: 重跑三个假设**（顺序执行，每个约 1-5 分钟）

```bash
uv run python scripts/backtest_futures.py --hypothesis F1
uv run python scripts/backtest_futures.py --hypothesis F2
uv run python scripts/backtest_futures.py --hypothesis F3
```

Expected: 每个打印 `✅ Fx 已写入 docs/data/Fx_backtest_result.json` + 结果摘要。若 akshare 指数/ETF 调用失败（东财端点诊断 ❌ 时）→ 报告并停，勿用旧 JSON 冒充。

- [ ] **Step 2: 数字同步（P0：全部数字从新 JSON 直引，禁止心算）**

用 Python 对照新旧 JSON 提取变化数字（写脚本跑，勿目视清单）：

```bash
uv run python - <<'EOF'
import json
from pathlib import Path
for name in ("F1", "F2", "F3"):
    p = Path("docs/data") / f"{name}_backtest_result.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    print(name, "keys:", list(d.get("scenarios", d.get("etfs", {}))))
EOF
```

然后逐处更新（以新 JSON 字段值为准）：
1. `host-docs/v0.2.6/F系列期货状态刻画报告_20260815.md`：F1（基差分位→ETF 收益表）、F2（deep_discount/premium 各 horizon 的 n/均值/胜率 + 无条件基线）、F3（cross_tab、n_events、Granger、降级裁定表述）全部段落替换为新 JSON 数字；"数据残缺"相关旧表述更新为"完整当月合约序列重建后"。
2. `host-docs/v0.2.6/股指期货数据融入ETF分析_调研方案_20260814.md`：
   - 126 行：`估值分位规则同款）；持仓量 20 日变化（对冲盘行为描述）；**条件句表述**` → `估值分位规则同款）；**条件句表述**`
   - 147 行 F2 行：`状态 = 贴水极值（分位 >90%/<10%）` → `状态 = 贴水极值（升序分位 <10% = 深贴水 / >90% = 升水）`
   - 148 行 F3 行：产出列加注 `（v0.2.6 修订：该口径裁定为展期节奏主导，从用户标签移除，仅保留回测刻画）`
3. `CHANGELOG.md`：F 系列段落旧数字（如 IF 深贴水 +5 胜率 69.2%、升水 47.8%、F3 up=0/down=1、n=136/78 等）替换为新 JSON 值；新增一行："F1/F2/F3 在完整当月合约序列（到期日边界重建，修复每月 ~40% 交易日缺失）上重跑，数字全部更新"。

- [ ] **Step 3: Commit**

```bash
git add docs/data/F1_backtest_result.json docs/data/F2_backtest_result.json docs/data/F3_backtest_result.json host-docs/v0.2.6/ CHANGELOG.md
git commit -m "fix: F1/F2/F3 完整序列重跑 + 报告/CHANGELOG 数字同步"
```

---

### Task 10: E 系列基线重跑 + scenario-plans 表重写

**Files:**
- Regenerate: `docs/data/scenario_baselines_E002_E007.json`
- Modify: `skills/lib/references/scenario-plans.md`（E 系列表）、`CHANGELOG.md`（E 系列段落）

- [ ] **Step 1: 重跑**

Run: `uv run python scripts/scenario_baselines.py`
Expected: 打印各预案 `n_hits`（E-002 应从 21→20、E-003/E-004 12→11，幻影 1990-12-19 事件消失；E-006 从 795 大幅下降至段首日数）、`+1/+3/+5` 统计、E-004 `touch_wave_target_60d`、baseline。

- [ ] **Step 2: scenario-plans.md 表重写**（读 40-70 行定位 E 系列表）

1. 表中 E-002~E-007 各行的 n/均值/胜率/E-004 触达比例全部替换为新 JSON 值（直引，禁心算）。
2. **E-006 裁决规则**：若新统计的"显著强于无条件基线"不再成立（按本文件自己的判定标准），该行与正文结论降级为"仅观察不作推断"（与 E-002~E-005 同表述），并在行尾加注"事件首日去重后样本自相关消除"。
3. 来源标注合规化：表头"触发点（科学计算）"与"情景映射位（Python 计算）"改为合规格式，如 `[来源: Python calc: 3浪高-(高-低)×0.382]` 式（每个触发位标注实际 formula）。
4. E-005 口径注记：触发位 4015.0 为硬编码上沿（文档若写"3983~4015 带内站稳"须改为与代码一致的"收盘站稳 4015 上沿"）。

- [ ] **Step 3: CHANGELOG E 系列段落同步**

旧数字（E-002 21、E-003 12、E-004 12/83.3%、E-005 9、E-006 795/2.11%/62%、E-007 26 等）替换为新 JSON 值；补一行"E-006/E-007 事件日改为状态段首日（重叠前向窗口自相关修复），E-006 结论按新样本重裁"。

- [ ] **Step 4: Commit**

```bash
git add docs/data/scenario_baselines_E002_E007.json skills/lib/references/scenario-plans.md CHANGELOG.md
git commit -m "fix: E 系列基线重跑 + scenario-plans 表重写（E-006 重裁）"
```

---

### Task 11: H6 重跑 + CHANGELOG 数字同步

**Files:**
- Regenerate: `docs/data/H6_backtest_result.json`
- Modify: `CHANGELOG.md`（H6 段落）

- [ ] **Step 1: 重跑**（全市场 800 抽样 + 沪深300，预计 10-30 分钟）

Run: `uv run python scripts/backtest_h6.py`
Expected: 打印 `meta.n_events_by_layer` 与各层 t/grade。gap 层事件数会因幽灵缺口消除而下降（旧 11166/15866）。

- [ ] **Step 2: CHANGELOG 同步**

以新 JSON 的 `meta.n_events_by_layer` / 各层 `mean_pct`/`t_nw`/`grade` 替换 CHANGELOG H6 段落旧数字（11166/15866、t −3.55/−3.89/−3.99、超额区间等）；裁决方向若变化（gap|trending 的 t 是否仍 ≥3）按新值如实更新，不得沿用旧裁决。

- [ ] **Step 3: Commit**

```bash
git add docs/data/H6_backtest_result.json CHANGELOG.md
git commit -m "fix: H6 重跑（幽灵缺口消除）+ CHANGELOG 数字同步"
```

---

### Task 12: 全量验证 + CHANGELOG 汇总 + 收尾

**Files:** `CHANGELOG.md`（顶部汇总条目）

- [ ] **Step 1: 全量 pytest**

Run: `uv run python -m pytest skills/lib/tests skills/invest-a-stock/tests skills/invest-a-etf/tests skills/invest-a-journal/tests -q`
Expected: 全绿（0 failed）。若有失败 → 逐项修复后重跑，不得带红提交。

- [ ] **Step 2: 残留引用终检**

```bash
grep -rn "持仓量 20 日变化\|futures_oi_change_pct" skills/ scripts/ --include="*.py" --include="*.md" | grep -v "backtest_prereg"
```
Expected: 空（预注册 F3_预注册.md 为冻结历史记录，保留）。

- [ ] **Step 3: CHANGELOG 顶部汇总条目**（v0.2.6 修订，日期 2026-08-16；逐条对应 finding 编号）：

```markdown
## v0.2.6 修订 2026-08-16 — /code-review max 10 项修复（数据层重建 + 全量重跑）

- 数据层：futures_daily 当月窗口按月划分 → 按前合约到期日划分，修复每月约 40% 交易日缺失（finding #1）；--force 全量重建并重跑 F1/F2/F3
- 口径：compound_oi_change 共享 helper（掩码/有效数阈值单份实现，finding #10）；OI 20 日变化从 journal/ETF/pulse 用户标签移除（与 F3 裁定一致，finding #2）
- E 系列：事件日统一"状态段首日"（幻影首行/重叠窗口修复，finding #3/#4）；E-004 60 日窗口 off-by-one（finding #5）；全量重跑，E-006 结论重裁
- H6：缺口扫描 g≥1 钳制，消除 highs[-1] 未来数据幽灵缺口（finding #6）；重跑
- F2/F3：事件日指数日历守卫（finding #8）；F3 基差/收益共用指数交易日历（finding #7）；F1/F2 分位 expanding-window 化（look-ahead 修复）
- 测试：ADX 独立手算 golden oracle（finding #9）；各修复配回归测试（新增/更新 N 条）
```

（"N 条"以实际新增测试数填写——`git diff --stat` 后跑 `grep -c "def test_"` 统计本轮新增）

- [ ] **Step 4: 最终 Commit**

```bash
git add CHANGELOG.md
git commit -m "fix: /code-review max 10 项修复收尾 — 全量 pytest + CHANGELOG 汇总"
```

---

## Self-Review 记录

1. **Spec 覆盖**：spec A→Task 2/8 ✓；B→Task 1/5/6 ✓；C→Task 3/10 ✓；D→Task 4/11 ✓；E→Task 5/9 ✓；F→Task 7 ✓；G→Task 6/9/10/11/12 ✓；H 测试表→各 Task Step 1 ✓；I 执行顺序→Task 8→9→10→11→12 ✓。
2. **占位符**：无 TBD/TODO；数据依赖数字（重跑结果）为运行时从 JSON 直引，计划中已标注"以新 JSON 字段值为准"的引用纪律，非占位。
3. **类型/命名一致性**：`compound_oi_change(vals, *, window, min_valid)` 在 Task 1 定义、Task 5 F3 调用签名一致 ✓；`expanding_percentile_rank(seq)` stats 定义与 F1/F2 调用一致 ✓；`_basis_state_after(basis_map, closes, all_dates, d, *, horizon)` 定义与测试一致 ✓；`touch_within(closes, hit_idx, targets, *, horizon, n)` 定义与测试一致 ✓；`clear_futures_daily` 测试与实现一致 ✓；所有测试期望值已由 Python 独立预演验证（ADX golden 匹配、H6 buggy/fixed 判别、E 系列向量、F2 事件集、basis_state_after 数值）。
