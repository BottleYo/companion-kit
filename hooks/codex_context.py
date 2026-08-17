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


def _record_health(hook_type: str) -> None:
    try:
        from companion_kit.hook_health import HookHealthStore

        HookHealthStore(plugin_root=_plugin_root()).record_success(hook_type)
    except Exception:
        # 健康回执只服务管理面板，写入失败不能改变宿主任务。
        return


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("hook_event_name") != "SessionStart":
            return 0

        package_root = _plugin_root() / "skills" / "virtual-companion" / "scripts"
        sys.path.insert(0, str(package_root))
        from companion_kit.companion_scope import (
            CompanionScopeError,
            CompanionScopeStore,
        )
        from companion_kit.codex_runtime import load_codex_runtime_context
        from companion_kit.hook_health import COMPANION_CONTEXT, SESSION_START

        _record_health(SESSION_START)
        session_id = str(payload.get("session_id") or "")
        try:
            is_companion_task = CompanionScopeStore(lock_timeout=0.25).is_bound(
                session_id
            )
        except CompanionScopeError:
            # Scope 损坏时按未绑定处理，绝不恢复成全局 Persona 注入。
            is_companion_task = False
        if not is_companion_task:
            return 0

        runtime = load_codex_runtime_context(include_identity=False)
        if runtime is None:
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
                    task_scope=session_id,
                ),
            }
        }
        _record_health(COMPANION_CONTEXT)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
    except Exception:
        # 日常对话不能被可选人格层阻断；诊断留给显式入口和管理面板。
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
