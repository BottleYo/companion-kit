#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


sys.dont_write_bytecode = True


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT", "").strip()
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def _record_session_start_health() -> None:
    try:
        from companion_kit.hook_health import HookHealthStore, SESSION_START

        HookHealthStore(plugin_root=_plugin_root()).record_success(SESSION_START)
    except Exception:
        # 健康回执只服务管理面板，写入失败不能吞掉 Persona 上下文。
        return


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("hook_event_name") != "SessionStart":
            return 0

        package_root = _plugin_root() / "skills" / "virtual-companion" / "scripts"
        sys.path.insert(0, str(package_root))
        from companion_kit.codex_runtime import load_codex_runtime_context

        runtime = load_codex_runtime_context()
        if runtime is None:
            _record_session_start_health()
            return 0
        result = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": runtime.render(
                    control_path=(
                        _plugin_root()
                        / "skills"
                        / "virtual-companion"
                        / "scripts"
                        / "companionctl.py"
                    ),
                    task_scope=str(payload.get("session_id") or ""),
                ),
            }
        }
        _record_session_start_health()
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
    except Exception:
        # 日常对话不能被可选人格层阻断；诊断留给显式入口和管理面板。
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
