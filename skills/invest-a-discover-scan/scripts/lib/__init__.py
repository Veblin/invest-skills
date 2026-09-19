"""discover-scan 内部 lib（顶层模块导入，非包导入）。

约定（同 invest-hk-stock）：本目录**不是** `lib` 包——`lib` 名字保留给 invest-a-stock。
各模块由 `discover_scan.py` 通过 `sys.path` 注入后按顶层名导入（`import pool` 等）。
"""
