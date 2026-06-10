# Changelog

## v0.2.0 (2026-06-10)

### 新增
- **环境诊断模块** (`env_check.py`)：代理检测、API 端点连通性、Python 依赖功能级检测
- **基础设施模块** (`data_utils.py`)：proxy bypass、code normalize、extract_metric 宽表提取、curl fallback、衍生指标计算
- **Web 搜索正式维度** (`web_research.py`)：查询模板 + 可信度分级标签 (🔵官方/🟡分析师/🔴传闻)
- **虚拟环境隔离**：引入 `uv` + `pyproject.toml` + `.venv/`，不再污染系统 Python
- 数据管道支持缓存 (`use_cache=True`) 和公司名/主题传递

### 改进
- `a_share_data.py` 模块级代理绕过（`trust_env=False`），解决 EastMoney API 被系统代理阻断
- `data_pipeline.py` 新增 `web_research` 维度（index 6.5）+ 8 维默认采集
- `collect_all()` 支持 `name` / `topic` / `use_cache` 参数

### 文档
- SKILL.md 更新快速开始为 `uv sync` + `uv run python`
- 执行计划-实施明细.md 新增 Phase 0.5（虚拟环境隔离）
- 新增 `.gitignore`（排除 `.venv/`、`.env`、`evidence/`）

---

## v0.1.0 (2026-06-09)

### 初始骨架
- **SKILL.md**：9 条 LAWs 输出契约、7 条反模式、引用格式规范、工作流四步描述
- **AGENTS.md**：五条硬约束、目标用户画像、设计哲学、数据源分层
- **README.md**：项目定位、快速开始、架构图
- **配置层**：`source_credibility.yaml` / `dimension_baselines.yaml` / `cross_validation_rules.yaml`
- **策略层**：9 个 `strategies/*.yaml`（七维度 + ETF）
- **知识库**：7 篇 `knowledge/*.md`
- **数据管道骨架**：`data_pipeline.py` (collect_all 框架) + 15 个 lib 模块骨架
