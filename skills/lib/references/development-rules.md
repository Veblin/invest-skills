# 开发规范 — Code Review 审查标准

> **来源**: 反复出现的 `/code-review` 缺陷模式汇总。
> **使用者**: `/code-review` skill、开发者自我审查、新代码提交前检查。
> **格式**: D1-D13 规则 ID，可被 review finding 引用（如 "违反 D1"）。
>
> 每条标注 🌐 = 跨语言通用 / 🐍 = Python 专项。

---

## 1. 空值处理

### D1 — 禁止 `or` 做数值/布尔值的 None-coalescing 🐍

**原则**: `0`、`0.0`、`False` 是合法的金融/逻辑值，但 Python `or` 将其视为 falsy。涉及数值、百分比、布尔值、资金流、比率的降级链，必须用显式 `is None` 检查。

**触发来源**: `angle-c-findings.json` F3-F6（系统性 falsy-zero 陷阱）。

```python
# ❌ 错误：0 / 0.0 / False 是合法值但会被 or 跳过
mf10 = _safe_num(mf_lr.get("net_sum_10d") or nb_lr.get("net_sum_10d"))
roe  = _safe_num(latest_fin.get("roe") or target.get("roe"))
ocf  = _safe_num(latest_fin.get("ocf") or latest_fin.get("n_cashflow_act"))
pcr_p = (pcr or {}).get("percentile_5y") or (pcr or {}).get("percentile_60d")

# ✅ 正确：显式 None 检查
_raw = mf_lr.get("net_sum_10d")
if _raw is None:
    _raw = nb_lr.get("net_sum_10d")
mf10 = _safe_num(_raw)
```

**适用范围**: 所有 `_safe_num()`、`safe_float()` 以及带降级链的数值提取。

**检测命令**:
```bash
grep -nE '_safe_num\(.*\bor\b' skills/ -r
grep -nE 'safe_float\(.*\bor\b' skills/ -r
```

---

### D2 — `dict.get(key, default)` 不防御 `value is None` 🐍

**原则**: `.get(key, default)` 只在 key 缺失时返回 default；若 key 存在但 value 为 `None`，返回 `None`。数据源经常返回 `{"key": None}` 表示"数据不可得"。

**触发来源**: `angle-c-findings.json` F1（None.get() AttributeError crash）。

```python
# ❌ 错误：coverage 存在但值为 None → .get("auto", 0) → AttributeError
risk_signals_n = risk_data.get("coverage", {}).get("auto", 0)

# ✅ 正确：(d.get("key") or {}) 模式 — None 和缺失 key 都被覆盖
risk_signals_n = (risk_data.get("coverage") or {}).get("auto", 0)
```

**注意**: `or {}` 模式仅对 dict/list 降级安全（空容器为 falsy 是期望行为）。对数值降级必须用 D1 的 `is None` 模式。

---

## 2. 边界条件

### D3 — 滑动窗口 = N+1 个数据点 🌐

**原则**: 计算"过去 N 期变化"需要起点 + N 个后续点 = N+1 行。任何 SQL `LIMIT N` 或 Python 切片 `[-N:]` 都只能拿到 N 个最新点。

**触发来源**: `angle-c-findings.json` F8（60d ETF 份额变动永远返回 None）。

```python
# ❌ 错误：_change(60) 需要 61 行，LIMIT 60 永远不够
rows = c.execute("SELECT ... LIMIT ?", (symbol, days)).fetchall()

# ✅ 正确
rows = c.execute("SELECT ... LIMIT ?", (symbol, days + 1)).fetchall()
```

**通用模式**: 任何 `def _change(window)` + `len(history) < window + 1` 的配对 — 查询侧和检查侧必须一致。

---

### D4 — 聚合数据必须标注覆盖范围 🌐

**原则**: 如果数据源只覆盖部分市场/交易所/品种，字段名和注释必须显式标注，下游比率必须基于估算全量值。禁止用部分数据冒充全量。

**触发来源**: `angle-c-findings.json` F9（total_turnover 仅含深交所 → 比率夸大 ~2x）。

**检查清单**:
- [ ] 字段名是否暗示全量但实际是部分？（如 `total_turnover` 实际是 `szse_turnover`）
- [ ] 注释是否标注了覆盖范围和数据缺口？
- [ ] 下游比率计算是否使用了全量估算值而非部分值？

---

## 3. 失败模式

### D5 — 空输入必须 fail loud 🌐

**原则**: 空列表/None 作为核心输入参数时，静默返回空结果是 bug。调用方不知道发生了什么，排查链条断裂。必须 raise `ValueError`（或至少 `logger.warning` + 返回 sentinel）。

**触发来源**: `angle-c-findings.json` F10（BaostockSource([]) 静默零数据）。

```python
# ❌ 错误：ts_codes=[] → 静默返回空 DataFrame，调用方无感知
return BaostockSource(ts_codes=ts_codes or [])

# ✅ 正确
if not ts_codes:
    raise ValueError("No ts_codes provided; cannot create data source")
```

---

### D6 — 空结果不应被缓存 🌐

**原则**: 非交易日/临时错误返回的 `[]`/`{}` 如果被正常 TTL 缓存，会阻止后续请求重新抓取。`if data is not None` 不足以防御 — 空集合是 truthy 检查的漏网之鱼。

**触发来源**: `angle-c-findings.json` F12（空 collector 结果被缓存）。

```python
# ❌ 错误：空集合通过 None 检查，被正常缓存
if data is not None:
    cache.set(key, data, ttl=3600)

# ✅ 正确：额外检查非空
if data is not None:
    if isinstance(data, (list, dict)) and len(data) == 0:
        logger.debug("skipping cache for empty result")
    else:
        cache.set(key, data, ttl=3600)
```

---

## 4. 可变性与并发

### D7 — 不修改共享存储返回的对象 🌐

**原则**: 从缓存/数据库/共享 dict 获取的对象可能在多处被引用。直接修改会污染持久化存储或破坏其他引用方的假设。需要附加元数据时先 copy。

**触发来源**: `angle-c-findings.json` F7（`_from_cache` 标记污染 JSON 缓存）。

```python
# ❌ 错误：直接修改缓存中的 dict → 可能被序列化到 JSON 持久化
data["_from_cache"] = True

# ✅ 正确：shallow-copy 后再标记
if isinstance(data, dict):
    data = dict(data)
    data["_from_cache"] = True
```

---

### D8 — 多线程 + 可变状态 = 必须加锁 🌐

**原则**: Python 的 `+=`/`-=` 不是原子操作。文件 `unlink` 与 `open` 并发会触发 `FileNotFoundError`。至少有：
- 计数器受 `threading.Lock` 保护
- `_load()` 捕获 `FileNotFoundError`（防御并发删除）
- LRU 清理与文件写入在同一临界区内

**触发来源**: `angle-c-findings.json` F11（DataCache 零线程安全）。

```python
class DataCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._hits = 0

    def get(self, ...):
        ...
        with self._lock:
            self._hits += 1  # 原子化计数器

    def set(self, ...):
        with self._lock:
            _atomic_write(...)
            _lru_cleanup()  # 写入与清理在同一临界区
```

---

### D9 — Context manager 的锁必须覆盖 yield 🌐

**原则**: 如果 `__enter__` 加锁后立即释放、`__exit__` 再加锁，多线程的 enter/exit 会交错。Monkey-patch/环境变量等全局状态可能在一个线程退出时被恢复，而其他线程仍在 yield 中使用。

**触发来源**: `angle-c-findings.json` F13（proxy 跨线程 patch 状态不一致）。

```python
# ❌ 错误：锁在 yield 期间释放 — 两个线程的 enter/exit 可交错
with _lock:
    _patch_enter()
try:
    yield
finally:
    with _lock:
        _patch_exit()

# ✅ 正确：锁覆盖整个 yield — 整个上下文是原子的
with _lock:
    _patch_enter()
    try:
        yield
    finally:
        _patch_exit()
```

---

## 5. 性能

### D10 — 昂贵纯函数不重复调用；O(N log N) 操作要节流 🌐

**原则**: 
- 同一函数内多次调用 `compute(kline)`（相同输入 → 相同输出）是浪费。计算一次，复用。
- 每次写入触发 `rglob("*.json")` + `sorted(..., key=stat)` + `unlink` 是不必要的。用计数器节流（如每 50 次写入执行一次清理）。

**触发来源**: `angle-c-findings.json` F14（3x compute）+ F15（每次 set 执行 LRU）。

---

## 6. 代码审查流程

### D11 — 修复一个 bug → 扫描全项目同类模式 🌐

**原则**: `/code-review` 发现的缺陷很少是孤立个案。如 `or` falsy-zero、`dict.get` None 陷阱等是复制粘贴的系统性问题。修复第一个实例后立即 `grep` 全项目。

**触发来源**: `angle-c-findings.json` 15 findings 中 5 个是同一 falsy-zero 模式的实例。

**标准流程**:
1. 理解 finding 的根因模式（不是修一个实例）
2. `grep` 搜索相同模式，列出所有命中
3. 逐一判断每个命中是否需要修复（不是所有 `or` 都是 bug）
4. 批量修复 → 添加 CLAUDE.md 规则防止复发

---

### D13 — 测试 mock 必须可验证生效 + 提交前跑「无凭据环境」套件 🌐

**原则**: 环境依赖的测试（本地过、CI 挂）源于 mock 未生效——被测函数从定义模块
的全局查找名字，而 patch 打在 re-export 包命名空间的**拷贝属性**上。collector
拆分后 `lib.collector` 经 `_legacy`/`__init__` 逐层 star-import，`lib.collector._xxx`
是无效 patch 目标（`collect_financials` 从 `_orchestrate` 全局查找）。

**触发来源**: test_v017 `dcf_preprocess` 测试 — patch `lib.collector._run_sources_parallel`
不生效，真实 `_run_sources_parallel` 执行：本地有 TUSHARE_TOKEN 碰巧通过，
CI 无源（'No data returned' 骨架）失败。

**标准流程**:
1. **patch 目标必须匹配消费方查找点**：私有名（下划线前缀）按约定模块内部使用，
   调用方是定义模块自己的全局 → 必须打定义模块命名空间（`lib.collector._orchestrate._xxx`
   / `_base.` / `_sources.` / `_legacy.`）；公共 API 名（如 `attach_phase2_extras`）
   消费方经包命名空间属性查找 → 包级 patch 有效。静态规则见 `test_test_hygiene.py`
2. **mock 输出带环境无关的独特标记并断言之**（如 `end_date: "20991231"`）：patch 失效时即使真实源成功，断言也失败，不会碰巧通过
3. **提交前以无凭据环境跑全仓套件**（等价 CI）：`TUSHARE_TOKEN="bogus" FRED_API_KEY="bogus" TAVILY_API_KEY="bogus" uv run python -m pytest -q` —— 任何依赖真实数据源才过的测试都会在此暴露

---

### D12 — WONTFIX 必须写理由 🌐

**原则**: 没有理由的 WONTFIX = 技术债。每个决定不修的 finding 必须标注：
- **不修理由**（如"需 profiler 确认后再优化"）
- **重新评估触发条件**（如"当缓存文件数超过 1000 时重新审视"）

**触发来源**: 历史 review finding 跟踪经验。

---

## 附录：快速检查清单

在提交代码前，逐条过一遍：

- [ ] D1: 有没有 `_safe_num(x or y)` ？→ 改为 `is None` 检查
- [ ] D2: 有没有 `.get(key, {}).get(key2` ？→ 确认 None 安全性
- [ ] D3: 有没有 window/limit/切片 差 1 ？→ 检查 N vs N+1
- [ ] D4: 聚合数据字段名是否完整标注覆盖范围？
- [ ] D5: 函数收到空输入会 fail loud 还是静默返回？
- [ ] D6: 空结果 `[]`/`{}` 是否被缓存？
- [ ] D7: 是否在修改从缓存/get 返回的 dict？
- [ ] D8: 新类有多线程风险吗？计数器/文件操作有锁吗？
- [ ] D9: Context manager 里的锁覆盖 yield 了吗？
- [ ] D10: 有没有同一个函数内重复调用昂贵纯函数？
- [ ] D11: 修复的 bug 模式在其他文件里还有吗？
- [ ] D12: 决定不修的 finding 写了理由吗？
- [ ] D13: 测试 mock 打在定义模块命名空间了吗？提交前跑过无凭据套件（`TUSHARE_TOKEN="bogus" ... pytest -q`）吗？
