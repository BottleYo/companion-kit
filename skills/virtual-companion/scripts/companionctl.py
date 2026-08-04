#!/usr/bin/env python3
"""从安装后的 Skill 包直接运行 Companion Kit。"""

import sys

sys.dont_write_bytecode = True

from companion_kit.cli import main


raise SystemExit(main())
