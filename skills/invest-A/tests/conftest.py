"""pytest 配置：确保 lib/ 可被测试导入。"""
import sys
from pathlib import Path

# 添加 scripts/ 路径
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
