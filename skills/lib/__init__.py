"""invest-a shared pure-function library.

Modules in this directory are importable directly via ``sys.path``
bootstrap (``_invest_path.py`` shims in each skill add this directory
to ``sys.path``).  No skill-local ``lib/`` should duplicate functions
that canonically live here.
"""

from __future__ import annotations
