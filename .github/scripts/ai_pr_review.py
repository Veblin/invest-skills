#!/usr/bin/env python3
"""GitHub Actions: AI pull-request review (stdlib only, multi-provider)."""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DIFF_PATH = ROOT / "pr.diff"
REVIEW_PATH = ROOT / "review.md"
VERDICT_PATH = ROOT / "review_verdict.txt"

MAX_DIFF_CHARS = 80_000
MAX_CONTEXT_CHARS = 24_000

# provider -> (default_model, env var names tried in order)
PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "default_model": "deepseek-v4-flash",
        "key_envs": ("DEEPSEEK_API_KEY", "AI_REVIEW_API_KEY"),
        "kind": "openai",
        "base_url": "https://api.deepseek.com/chat/completions",
    },
    "gemini": {
        "default_model": "gemini-2.0-flash",
        "key_envs": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AI_REVIEW_API_KEY"),
        "kind": "gemini",
    },
    "openai": {
        "default_model": "gpt-4o-mini",
        "key_envs": ("OPENAI_API_KEY", "AI_REVIEW_API_KEY"),
        "kind": "openai",
        "base_url": "https://api.openai.com/v1/chat/completions",
    },
    "openrouter": {
        "default_model": "deepseek/deepseek-v4-flash",
        "key_envs": ("OPENROUTER_API_KEY", "AI_REVIEW_API_KEY"),
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "extra_headers": {"HTTP-Referer": "https://github.com/Veblin/invest-skills"},
    },
    "anthropic": {
        "default_model": "claude-sonnet-4-20250514",
        "key_envs": ("ANTHROPIC_API_KEY", "AI_REVIEW_API_KEY"),
        "kind": "anthropic",
    },
}

DEFAULT_PROVIDER = "deepseek"

SYSTEM_PROMPT = """\
你是 invest-A 仓库的 PR 代码审查助手。invest-A 是 A 股个股**学习工具**（非投资决策工具）。

审查范围：**仅审查 PR diff 中的变更**，不要臆测未改动的代码。

## 项目硬约束（AGENTS.md 摘要）
1. 禁止荐股：不得出现买卖/持有建议、目标价、仓位建议
2. LLM 不可作为投资决策主要信源
3. 分析必须依赖可追溯数据源
4. 无社交功能
5. 多 harness 兼容、数据源须有 fallback

## 输出契约（SKILL.md 9 条 LAWs）
- LAW 1: 每条分析论述必须引用数据来源
- LAW 2: 统一报告结构
- LAW 3: 区分事实与判断
- LAW 4: 首尾风险提示
- LAW 5: 并行取证，非串行降级
- LAW 6: 禁止买卖建议、目标价、仓位建议
- LAW 7: 每个数字可追溯
- LAW 8: 每维度末尾待验证项
- LAW 9: 无数据支撑不输出分析

## 代码审查重点
- 正确性、边界条件、类型安全、回归风险
- 数据采集：并行非串行回退（LAW 5）
- 报告/渲染：LAW 6 违规用语（金叉/死叉/买入信号等）
- 测试是否覆盖真实数据格式
- 是否泄露 API Key 或敏感信息

## 输出格式（必须严格遵守）

先写 2-4 句 Summary，然后：

### Findings

若无问题，写：`无 blocking 问题。`

若有問題，用 Markdown 表格（按 Severity 降序）：

| Severity | File:Line | Finding | Suggestion |
|----------|-----------|---------|------------|
| Critical/High/Medium/Low | path:line | 问题描述 | 修复建议 |

Severity 定义：
- Critical: 安全漏洞、数据丢失、必现崩溃
- High: 明显逻辑错误、LAW 6 违规、严重回归
- Medium: 边界 case、测试缺口
- Low: 风格、文档

最后单独一行（机器解析用，必须存在）：
VERDICT: APPROVE | REQUEST_CHANGES | COMMENT

规则：
- 存在 Critical 或 High → VERDICT: REQUEST_CHANGES
- 仅 Medium/Low 或无问题 → VERDICT: COMMENT
- 仅当 diff 极小且完全无问题时才用 VERDICT: APPROVE
"""


def _read_limited(path: Path, limit: int) -> str:
    if not path.is_file():
        return f"(file not found: {path})"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... [truncated, {len(text) - limit} chars omitted]"


def _parse_verdict(body: str) -> str:
    match = re.search(r"^VERDICT:\s*(APPROVE|REQUEST_CHANGES|COMMENT)\s*$", body, re.MULTILINE)
    if not match:
        return "COMMENT"
    return match.group(1)


def _strip_verdict_line(body: str) -> str:
    return re.sub(r"^VERDICT:\s*(APPROVE|REQUEST_CHANGES|COMMENT)\s*$", "", body, flags=re.MULTILINE).strip()


def _resolve_api_key(key_envs: tuple[str, ...]) -> str:
    for name in key_envs:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _http_json(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 120,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc


def _call_openai_compatible(
    *,
    api_key: str,
    model: str,
    user_prompt: str,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        **(extra_headers or {}),
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    data = _http_json(url=base_url, payload=payload, headers=headers)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"API returned no choices: {json.dumps(data)[:500]}")
    message = choices[0].get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        raise RuntimeError(f"API returned empty content: {json.dumps(data)[:500]}")
    return text


def _call_gemini(*, api_key: str, model: str, user_prompt: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"maxOutputTokens": 4096},
    }
    data = _http_json(url=url, payload=payload, headers={})
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {json.dumps(data)[:500]}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text_parts = [p.get("text", "") for p in parts if p.get("text")]
    if not text_parts:
        raise RuntimeError(f"Gemini returned no text: {json.dumps(data)[:500]}")
    return "\n".join(text_parts).strip()


def _call_anthropic(*, api_key: str, model: str, user_prompt: str) -> str:
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    data = _http_json(
        url="https://api.anthropic.com/v1/messages",
        payload=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    blocks = data.get("content") or []
    text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    if not text_parts:
        raise RuntimeError(f"Anthropic returned no text: {json.dumps(data)[:500]}")
    return "\n".join(text_parts).strip()


def _call_provider(*, provider: str, api_key: str, model: str, user_prompt: str) -> str:
    cfg = PROVIDERS[provider]
    kind = cfg["kind"]
    if kind == "openai":
        return _call_openai_compatible(
            api_key=api_key,
            model=model,
            user_prompt=user_prompt,
            base_url=cfg["base_url"],
            extra_headers=cfg.get("extra_headers"),
        )
    if kind == "gemini":
        return _call_gemini(api_key=api_key, model=model, user_prompt=user_prompt)
    if kind == "anthropic":
        return _call_anthropic(api_key=api_key, model=model, user_prompt=user_prompt)
    raise RuntimeError(f"Unsupported provider kind: {kind}")


def _skip_review(message: str) -> int:
    REVIEW_PATH.write_text(f"## AI Code Review\n\n_{message}_\n", encoding="utf-8")
    VERDICT_PATH.write_text("COMMENT", encoding="utf-8")
    return 0


def _env_or_default(name: str, default: str) -> str:
    """Read env var; treat missing or blank as default (GitHub vars expand to '')."""
    value = os.environ.get(name, "").strip()
    return value or default


def main() -> int:
    provider = _env_or_default("AI_REVIEW_PROVIDER", DEFAULT_PROVIDER).lower()
    if provider not in PROVIDERS:
        print(f"Unknown AI_REVIEW_PROVIDER={provider!r}", file=sys.stderr)
        return 1

    cfg = PROVIDERS[provider]
    api_key = _resolve_api_key(cfg["key_envs"])
    if not api_key:
        key_names = " / ".join(f"`{n}`" for n in cfg["key_envs"])
        return _skip_review(f"Skipped: no API key configured ({key_names}).")

    if not DIFF_PATH.is_file():
        print(f"Missing {DIFF_PATH}", file=sys.stderr)
        return 1

    diff = DIFF_PATH.read_text(encoding="utf-8", errors="replace")
    if not diff.strip():
        return _skip_review("No file changes to review.")

    agents = _read_limited(ROOT / "AGENTS.md", MAX_CONTEXT_CHARS)
    skill = _read_limited(ROOT / "skills/invest-A/SKILL.md", MAX_CONTEXT_CHARS)
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + f"\n\n... [diff truncated, {len(diff) - MAX_DIFF_CHARS} chars omitted]"

    pr_title = os.environ.get("PR_TITLE", "")
    pr_number = os.environ.get("PR_NUMBER", "")
    base_ref = os.environ.get("BASE_REF", "main")
    head_ref = os.environ.get("HEAD_REF", "")
    model = _env_or_default("AI_REVIEW_MODEL", cfg["default_model"])

    user_prompt = textwrap.dedent(f"""\
        Review this pull request for repository invest-A.

        PR #{pr_number}: {pr_title}
        Base: {base_ref} ← Head: {head_ref}

        ## AGENTS.md
        {agents}

        ## SKILL.md (includes LAWs)
        {skill}

        ## Git diff (base...HEAD)
        ```diff
        {diff}
        ```
    """)

    print(f"Calling provider={provider} model={model} ...")
    raw_review = _call_provider(provider=provider, api_key=api_key, model=model, user_prompt=user_prompt)
    verdict = _parse_verdict(raw_review)
    review_body = _strip_verdict_line(raw_review)

    header = (
        "## 🤖 AI Code Review\n\n"
        f"> Provider: `{provider}` | Model: `{model}` | Verdict: **{verdict}**\n\n"
        "_Advisory only — merge still requires human judgment and green CI._\n\n"
    )
    REVIEW_PATH.write_text(header + review_body + "\n", encoding="utf-8")
    VERDICT_PATH.write_text(verdict, encoding="utf-8")
    print(f"Review written to {REVIEW_PATH} (verdict={verdict})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
