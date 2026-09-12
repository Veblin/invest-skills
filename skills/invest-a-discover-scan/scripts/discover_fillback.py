#!/usr/bin/env python3
"""invest-a-discover-scan 回填脚本 — **v0.2 占位桩**。

本文件在 v0.1 只保留接口与版权说明，**不实现回填**：
- 回填需要 T+90/180 的相对基准超额（池内等权 / 沪深300 双行），并写回快照 `fillback` 字段
- 设计 §8 的阈值裁决依赖回填数据；未回填前禁止改阈值（`rules_version` 锚点）

退出码：**2 = 功能未实现**（设计 §3 约定，勿改成 0——0 会让调用方以为回填成功了）。
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="discover-scan 回填（v0.2；v0.1 未实现，退出码 2）")
    p.add_argument("--horizon", type=int, default=90, help="回填窗口（交易日，默认 90）")
    args = p.parse_args(argv)
    print(f"未实现（v0.2 计划）：fillback --horizon {args.horizon}\n"
          "本版只落快照；回填需 T+90/180 相对基准超额（池内等权 / 沪深300 双行），"
          "结论随回填窗进 v0.3.1（设计 §8 / §11）。", file=sys.stdout)
    return 2


if __name__ == "__main__":
    sys.exit(main())
