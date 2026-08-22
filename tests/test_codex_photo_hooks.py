from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from companion_kit.codex_image_receipts import CodexImageReceiptStore
from companion_kit.companion_scope import CompanionScopeStore
from companion_kit.codex_turn import make_turn_token
from companion_kit.daily_look_store import DailyLookStore
from companion_kit.daily_look import DailyLookDirective, DailyLookProposal
from companion_kit.file_lock import exclusive_file_lock
from companion_kit.hook_health import inspect_hook_bundle
from companion_kit.image_assets import ImageAssetStore
from companion_kit.initializer import initialize_profile
from companion_kit.photo_moment import PhotoMoment, encode_photo_envelope
from companion_kit.photo_moment_store import PhotoMomentStore
from companion_kit.pending_photo_context import PendingPhotoContextStore
from companion_kit.profile_store import ProfileStore
from tests.png_fixture import tiny_png


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "skills" / "virtual-companion"
PROMPT_HOOK = PROJECT_ROOT / "hooks" / "codex_prompt_context.py"
PRE_TOOL_HOOK = PROJECT_ROOT / "hooks" / "codex_image_guard.py"
POST_TOOL_HOOK = PROJECT_ROOT / "hooks" / "codex_image_receipt.py"


def _moment(*, mode: str = "new", identity_version: int = 1) -> PhotoMoment:
    return PhotoMoment.from_dict(
        {
            "mode": mode,
            "scene": "window",
            "activity": "getting_ready",
            "framing": "half",
            "hairstyle": "tied",
            "expression": "playful",
            "makeup": "natural",
            "portrait_dynamics": "side_glance_half_smile",
            "time_band": "day",
            "intimacy_band": "everyday",
            "caption_act": "unfinished_thought",
            "identity_version": identity_version,
        }
    )


def _run_hook(
    path: Path,
    payload: dict[str, object],
    environment: dict[str, str],
    *,
    timeout: float = 5.0,
):
    return subprocess.run(
        ["python3", str(path)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        timeout=timeout,
    )


def _persona_identity_snapshot(home: Path) -> dict[str, bytes]:
    selected = (
        home / "profiles",
        home / "private" / "images",
        home / "private" / "relationships.sqlite3",
    )
    result: dict[str, bytes] = {}
    for candidate in selected:
        if candidate.is_file():
            result[str(candidate.relative_to(home))] = candidate.read_bytes()
        elif candidate.is_dir():
            for path in sorted(candidate.rglob("*")):
                if path.is_file() and not path.name.endswith(".lock"):
                    result[str(path.relative_to(home))] = path.read_bytes()
    return result


def _lock_identity(home: Path):
    profile_path = home / "profiles" / "default.toml"
    initialize_profile(
        skill_root=SKILL_ROOT,
        template_id="warm_healer",
        display_name="小禾",
        output=profile_path,
    )
    profiles = ProfileStore(skill_root=SKILL_ROOT, profile_path=profile_path)
    snapshot = profiles.read()
    assert snapshot is not None
    assets = ImageAssetStore(home / "private" / "images")
    candidate = assets.store_candidate(
        profile_id=snapshot.profile.id,
        identity_version=1,
        image_bytes=tiny_png(),
        task_scope="identity-setup",
    )
    primary = assets.confirm_candidate(
        candidate_id=candidate.candidate_id,
        profile_id=snapshot.profile.id,
        identity_version=1,
        task_scope="identity-setup",
    )
    profiles.bind_reference(
        reference_id=primary.reference_id,
        identity_version=1,
        expected_version=snapshot.version,
    )
    pack = assets.resolve_identity_pack(
        primary_reference_id=primary.reference_id,
        profile_id=snapshot.profile.id,
        identity_version=1,
    )
    return pack.members[0]


class CodexPhotoHookTests(unittest.TestCase):
    def _environment(self, root: Path) -> tuple[dict[str, str], Path, Path]:
        home = root / "companion-home"
        codex_home = root / "codex-home"
        environment = os.environ.copy()
        environment.update(
            {
                "COMPANION_HOME": str(home),
                "CODEX_HOME": str(codex_home),
                "PLUGIN_ROOT": str(PROJECT_ROOT),
            }
        )
        environment.pop("COMPANION_PROFILE", None)
        return environment, home, codex_home

    def _prompt_with_envelope(
        self,
        *,
        home: Path,
        session_id: str,
        turn_id: str,
        mode: str,
        prompt: str,
        identity_version: int = 1,
    ) -> str:
        digest = inspect_hook_bundle(PROJECT_ROOT).hook_bundle_digest
        token = make_turn_token(
            session_id=session_id,
            turn_id=turn_id,
            mode=mode,
            hook_bundle_digest=digest,
        )
        with patch.dict(
            os.environ,
            {"COMPANION_HOME": str(home)},
            clear=False,
        ):
            PhotoMomentStore(lock_timeout=0.25).issue_turn(
                profile_id="companion",
                session_id=session_id,
                turn_id=turn_id,
                mode=mode,
                turn_token=token,
            )
        return prompt + encode_photo_envelope(
            token,
            _moment(mode=mode, identity_version=identity_version),
        )

    def test_non_photo_prompt_is_silent_and_has_no_disk_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "generic-session",
                    "turn_id": "generic-turn",
                    "prompt": "给产品落地页生成一张抽象背景图",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            self.assertFalse((home / "private" / "photo-moments").exists())

    def test_photo_prompt_loads_identity_only_for_that_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            primary = _lock_identity(home)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "photo-session",
                    "turn_id": "photo-turn",
                    "prompt": "我想看你现在的样子，拍一张吧",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            output = json.loads(completed.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn(str(primary.path), context)
            self.assertIn("COMPANION_KIT_PHOTO_V4", context)
            self.assertIn('"action":"use_daily"', context)
            self.assertIn("直接调用", context)
            self.assertNotIn("我想看你现在的样子", context)
            self.assertNotIn("OPENAI_API_KEY", context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                ticket = PhotoMomentStore().turn_ticket(
                    session_id="photo-session",
                    turn_id="photo-turn",
                )
            self.assertIsNotNone(ticket)
            self.assertEqual(ticket.mode, "new")
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                today = DailyLookStore().current(profile_id="companion")
            self.assertIsNotNone(today)
            self.assertIn(today.look_id, context)

    def test_explicit_persona_creation_prompt_does_not_bypass_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            primary = _lock_identity(home)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "persona-create-session",
                    "turn_id": "persona-create-turn",
                    "prompt": "帮我生成一张你的自拍",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn(str(primary.path), context)
            self.assertIn("COMPANION_KIT_PHOTO_V4", context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                ticket = PhotoMomentStore().turn_ticket(
                    session_id="persona-create-session",
                    turn_id="persona-create-turn",
                )
            self.assertIsNotNone(ticket)
            self.assertEqual(ticket.mode, "new")

    def test_bound_ootd_question_loads_today_look_without_starting_imagegen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                CompanionScopeStore().bind("ootd-session")

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "ootd-session",
                    "turn_id": "ootd-turn",
                    "prompt": "你今天穿什么？今日 OOTD 是什么",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("今天的穿搭卡", context)
            self.assertNotIn("imagegen", context)
            self.assertNotIn("COMPANION_KIT_PHOTO", context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                self.assertIsNotNone(
                    DailyLookStore().current(profile_id="companion")
                )
                self.assertIsNone(
                    PhotoMomentStore().turn_ticket(
                        session_id="ootd-session",
                        turn_id="ootd-turn",
                    )
                )
                self.assertIsNotNone(
                    PendingPhotoContextStore().peek(
                        profile_id="companion",
                        session_id="ootd-session",
                    )
                )

    def test_bound_ootd_short_followup_uses_daily_look_and_primary_face(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            primary = _lock_identity(home)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                CompanionScopeStore().bind("ootd-followup-session")

            ootd = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "ootd-followup-session",
                    "turn_id": "ootd-question-turn",
                    "prompt": "今日的 OOTD 是什么",
                },
                environment,
            )
            look_context = json.loads(ootd.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            durable_before = _persona_identity_snapshot(home)
            followup = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "ootd-followup-session",
                    "turn_id": "ootd-photo-turn",
                    "prompt": "拍吧",
                },
                environment,
            )

            self.assertEqual(followup.returncode, 0)
            self.assertEqual(followup.stderr, "")
            photo_context = json.loads(followup.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("COMPANION_KIT_PHOTO_V4", photo_context)
            self.assertIn(str(primary.path), photo_context)
            self.assertIn("referenced_image_paths", photo_context)
            self.assertIn("只生成一张", photo_context)
            self.assertIn("核心单品", photo_context)
            self.assertIn("核心单品", look_context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                ticket = PhotoMomentStore().turn_ticket(
                    session_id="ootd-followup-session",
                    turn_id="ootd-photo-turn",
                )
                self.assertIsNotNone(ticket)
                self.assertIsNone(
                    PendingPhotoContextStore().peek(
                        profile_id="companion",
                        session_id="ootd-followup-session",
                    )
                )
                look = DailyLookStore().current(profile_id="companion")
            assert ticket is not None
            assert look is not None
            guarded = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "ootd-followup-session",
                    "turn_id": "ootd-photo-turn",
                    "tool_use_id": "ootd-photo-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": "今日穿搭的自然自拍"
                        + encode_photo_envelope(
                            ticket.turn_token,
                            _moment(),
                            daily_look=DailyLookDirective(
                                action="use_daily",
                                look_id=look.look_id,
                                proposal=None,
                            ),
                        ),
                        "referenced_image_paths": [str(primary.path)],
                    },
                },
                environment,
            )
            guarded_output = json.loads(guarded.stdout)["hookSpecificOutput"]
            self.assertEqual(guarded_output["permissionDecision"], "allow")
            self.assertEqual(
                guarded_output["updatedInput"]["referenced_image_paths"],
                [str(primary.path)],
            )
            self.assertEqual(_persona_identity_snapshot(home), durable_before)

    def test_bound_explicit_short_photo_request_does_not_need_pending_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            primary = _lock_identity(home)
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                CompanionScopeStore().bind("short-photo-session")

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "short-photo-session",
                    "turn_id": "short-photo-turn",
                    "prompt": "拍一张我看看",
                },
                environment,
            )

            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("COMPANION_KIT_PHOTO_V4", context)
            self.assertIn(str(primary.path), context)

    def test_bare_short_photo_followup_without_pending_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                CompanionScopeStore().bind("no-pending-session")

            bound = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "no-pending-session",
                    "turn_id": "bound-bare-turn",
                    "prompt": "拍吧",
                },
                environment,
            )
            unbound = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "unbound-session",
                    "turn_id": "unbound-bare-turn",
                    "prompt": "拍吧",
                },
                environment,
            )

            self.assertEqual(bound.stdout, "")
            self.assertEqual(unbound.stdout, "")

    def test_obvious_non_photo_work_clears_pending_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                CompanionScopeStore().bind("pending-clear-session")
                PendingPhotoContextStore().remember(
                    profile_id="companion",
                    session_id="pending-clear-session",
                    source="photo_setup",
                )

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "pending-clear-session",
                    "turn_id": "coding-turn",
                    "prompt": "帮我修复这段代码的报错",
                },
                environment,
            )

            self.assertEqual(completed.stdout, "")
            with patch.dict(os.environ, {"COMPANION_HOME": str(home)}, clear=True):
                self.assertIsNone(
                    PendingPhotoContextStore().peek(
                        profile_id="companion",
                        session_id="pending-clear-session",
                    )
                )

    def test_unbound_ootd_phrase_is_silent_and_does_not_create_a_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "ordinary-session",
                    "turn_id": "ordinary-turn",
                    "prompt": "你今天穿什么？",
                },
                environment,
            )

            self.assertEqual(completed.stdout, "")
            self.assertFalse((home / "private" / "daily-looks").exists())

    def test_ootd_software_discussion_does_not_load_persona_or_create_a_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "ootd-design-session",
                    "turn_id": "ootd-design-turn",
                    "prompt": "分析一下今天的 OOTD 功能该怎么设计和测试",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertFalse((home / "private" / "daily-looks").exists())

    def test_explicit_identity_enhancement_loads_only_the_requested_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)

            completed = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "enhancement-session",
                    "turn_id": "enhancement-turn",
                    "prompt": "补一张全身体型参考",
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            context = json.loads(completed.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("补充体型", context)
            self.assertIn("--role body_shape", context)
            self.assertNotIn("--role profile_face", context)

    def test_previous_image_edit_is_signed_only_for_latest_companion_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            result = root / "latest.png"
            result.write_bytes(b"companion-result")
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                store = PhotoMomentStore()
                store.record_image_result(
                    profile_id="companion",
                    session_id="edit-session",
                    paths=(result,),
                    is_companion=True,
                    identity_version=1,
                )

            companion_edit = _run_hook(
                PROMPT_HOOK,
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "edit-session",
                    "turn_id": "companion-edit-turn",
                    "prompt": "把上一张改成短发",
                },
                environment,
            )
            context = json.loads(companion_edit.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn("明确编辑上一张", context)

            for index, prompt in enumerate(
                (
                    "把上一张产品模特的衣服换成蓝色",
                    "把你刚才那张猫图换成夜景",
                    "把你这张风景图调亮",
                )
            ):
                generic_with_marker = _run_hook(
                    PROMPT_HOOK,
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "edit-session",
                        "turn_id": f"generic-marker-turn-{index}",
                        "prompt": prompt,
                    },
                    environment,
                )
                self.assertEqual(generic_with_marker.stdout, "")

            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                store.record_image_result(
                    profile_id="companion",
                    session_id="edit-session",
                    paths=(result,),
                    is_companion=False,
                    identity_version=1,
                )
            for index, prompt in enumerate(
                (
                    "把上一张改成短发",
                    "把你刚才那张猫图的表情改萌一点",
                    "把你上一张自拍改成短发",
                )
            ):
                generic_edit = _run_hook(
                    PROMPT_HOOK,
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "edit-session",
                        "turn_id": f"generic-edit-turn-{index}",
                        "prompt": prompt,
                    },
                    environment,
                )
                self.assertEqual(generic_edit.stdout, "")

    def test_strong_persona_edit_stays_protected_when_marker_is_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, codex_home = self._environment(root)
            _lock_identity(home)
            bundle_digest = inspect_hook_bundle(PROJECT_ROOT).hook_bundle_digest

            for state in ("missing", "expired", "corrupt"):
                with self.subTest(state=state):
                    session_id = f"strong-edit-{state}-session"
                    turn_id = f"strong-edit-{state}-turn"
                    target = codex_home / "generated_images" / f"{state}.png"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(tiny_png(metadata=state.encode("ascii")))
                    with patch.dict(
                        os.environ,
                        {
                            "COMPANION_HOME": str(home),
                            "CODEX_HOME": str(codex_home),
                        },
                        clear=True,
                    ):
                        if state != "missing":
                            CodexImageReceiptStore().record(
                                session_id=session_id,
                                tool_use_id=f"{state}-source-call",
                                paths=(target,),
                            )
                        if state == "expired":
                            PhotoMomentStore(
                                clock=lambda: datetime.now(UTC)
                                - timedelta(hours=3)
                            ).record_image_result(
                                profile_id="companion",
                                session_id=session_id,
                                paths=(target,),
                                is_companion=True,
                                identity_version=1,
                            )
                        elif state == "corrupt":
                            moment_store = PhotoMomentStore()
                            moment_store.record_image_result(
                                profile_id="companion",
                                session_id=session_id,
                                paths=(target,),
                                is_companion=True,
                                identity_version=1,
                            )
                            result_path = next(
                                moment_store.result_root.glob("*.json")
                            )
                            result_path.write_text("{broken", encoding="utf-8")

                    submitted = _run_hook(
                        PROMPT_HOOK,
                        {
                            "hook_event_name": "UserPromptSubmit",
                            "session_id": session_id,
                            "turn_id": turn_id,
                            "prompt": "把你上一张自拍改成短发",
                        },
                        environment,
                    )
                    context = json.loads(submitted.stdout)["hookSpecificOutput"][
                        "additionalContext"
                    ]
                    self.assertIn("明确编辑上一张", context)

                    token = make_turn_token(
                        session_id=session_id,
                        turn_id=turn_id,
                        mode="edit_previous",
                        hook_bundle_digest=bundle_digest,
                    )
                    guarded = _run_hook(
                        PRE_TOOL_HOOK,
                        {
                            "hook_event_name": "PreToolUse",
                            "session_id": session_id,
                            "turn_id": turn_id,
                            "tool_use_id": f"{state}-edit-call",
                            "tool_name": "image_gen__imagegen",
                            "tool_input": {
                                "prompt": "把你上一张自拍改成短发"
                                + encode_photo_envelope(
                                    token,
                                    _moment(mode="edit_previous"),
                                ),
                                "referenced_image_paths": [str(target)],
                            },
                        },
                        environment,
                    )
                    decision = json.loads(guarded.stdout)["hookSpecificOutput"]
                    self.assertEqual(decision["permissionDecision"], "deny")
                    self.assertNotIn(
                        str(target),
                        json.dumps(decision, ensure_ascii=False),
                    )

    def test_generic_imagegen_without_valid_envelope_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "generic-session",
                    "turn_id": "generic-turn",
                    "tool_use_id": "generic-image-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": "抽象蓝色产品背景",
                        "num_last_images_to_include": 1,
                    },
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")

    def test_photo_ticket_cannot_run_without_its_control_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            self._prompt_with_envelope(
                home=home,
                session_id="photo-session",
                turn_id="photo-turn",
                mode="new",
                prompt="自然自拍",
            )

            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "photo-session",
                    "turn_id": "photo-turn",
                    "tool_use_id": "photo-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {"prompt": "自然自拍"},
                },
                environment,
            )

            decision = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertIn("缺少控制信封", decision["permissionDecisionReason"])

    def test_control_envelope_without_issued_ticket_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            session_id = "forged-session"
            turn_id = "forged-turn"
            token = make_turn_token(
                session_id=session_id,
                turn_id=turn_id,
                mode="new",
                hook_bundle_digest=inspect_hook_bundle(PROJECT_ROOT).hook_bundle_digest,
            )

            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": "forged-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": "自然自拍" + encode_photo_envelope(token, _moment())
                    },
                },
                environment,
            )

            decision = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertIn("票据", decision["permissionDecisionReason"])

    def test_outdated_identity_version_is_denied_before_imagegen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            session_id = "stale-identity-session"
            turn_id = "stale-identity-turn"

            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": "stale-identity-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": self._prompt_with_envelope(
                            home=home,
                            session_id=session_id,
                            turn_id=turn_id,
                            mode="new",
                            prompt="自然自拍",
                            identity_version=2,
                        )
                    },
                },
                environment,
            )

            decision = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertIn("身份版本", decision["permissionDecisionReason"])

    def test_unlocked_candidate_still_obeys_relationship_and_caption_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=home / "profiles" / "default.toml",
            )
            session_id = "candidate-session"
            turn_id = "candidate-turn"

            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": "candidate-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": self._prompt_with_envelope(
                            home=home,
                            session_id=session_id,
                            turn_id=turn_id,
                            mode="new",
                            prompt="自然的候选人物照片",
                        ),
                        "referenced_image_paths": [
                            str(root / "old-generated-face.png")
                        ],
                        "num_last_images_to_include": 1,
                    },
                },
                environment,
            )

            output = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"], "allow")
            self.assertIn("普通日常分享", output["updatedInput"]["prompt"])
            self.assertNotIn("COMPANION_KIT_PHOTO_V4", output["updatedInput"]["prompt"])
            self.assertNotIn("referenced_image_paths", output["updatedInput"])
            self.assertNotIn("num_last_images_to_include", output["updatedInput"])
            self.assertNotIn("additionalContext", output)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                pending = PhotoMomentStore().peek(
                    profile_id="companion",
                    session_id=session_id,
                    tool_use_id="candidate-call",
                )
            self.assertIsNotNone(pending)

    def test_unlocked_explicit_edit_uses_only_verified_latest_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, codex_home = self._environment(root)
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=home / "profiles" / "default.toml",
            )
            previous = codex_home / "generated_images" / "candidate.png"
            previous.parent.mkdir(parents=True)
            previous.write_bytes(tiny_png(metadata=b"candidate"))
            session_id = "candidate-edit-session"
            turn_id = "candidate-edit-turn"
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(home),
                    "CODEX_HOME": str(codex_home),
                },
                clear=True,
            ):
                CodexImageReceiptStore().record(
                    session_id=session_id,
                    tool_use_id="candidate-source-call",
                    paths=(previous,),
                )
                PhotoMomentStore().record_image_result(
                    profile_id="companion",
                    session_id=session_id,
                    paths=(previous,),
                    is_companion=True,
                    identity_version=1,
                )

            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": "candidate-edit-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": self._prompt_with_envelope(
                            home=home,
                            session_id=session_id,
                            turn_id=turn_id,
                            mode="edit_previous",
                            prompt="只把上一张改成短发",
                        ),
                        "referenced_image_paths": [str(previous)],
                    },
                },
                environment,
            )

            decision = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "allow")
            self.assertEqual(
                decision["updatedInput"]["referenced_image_paths"],
                [str(previous)],
            )

    def test_new_photo_forces_canonical_identity_and_removes_image_chaining(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, codex_home = self._environment(root)
            primary = _lock_identity(home)
            old_image = codex_home / "generated_images" / "old.png"
            old_image.parent.mkdir(parents=True)
            old_image.write_bytes(tiny_png(metadata=b"old"))
            session_id = "photo-session"
            turn_id = "photo-turn"

            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": "new-photo-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": self._prompt_with_envelope(
                            home=home,
                            session_id=session_id,
                            turn_id=turn_id,
                            mode="new",
                            prompt="自然生活感的半身自拍",
                        ),
                        "referenced_image_paths": [str(old_image)],
                        "num_last_images_to_include": 1,
                    },
                },
                environment,
            )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            output = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"], "allow")
            updated = output["updatedInput"]
            self.assertEqual(updated["referenced_image_paths"], [str(primary.path)])
            self.assertNotIn("num_last_images_to_include", updated)
            self.assertNotIn(str(old_image), json.dumps(updated, ensure_ascii=False))
            self.assertNotIn("COMPANION_KIT_PHOTO_V4", updated["prompt"])
            self.assertIn("只固定脸部身份", updated["prompt"])
            self.assertIn("发型", updated["prompt"])
            self.assertIn("表情", updated["prompt"])
            self.assertIn("本次最终妆容", updated["prompt"])
            self.assertIn("本次面部动态", updated["prompt"])
            self.assertIn("视线从侧面回到镜头附近", updated["prompt"])
            self.assertIn("偏高挑、修长", updated["prompt"])
            self.assertIn("严禁大头娃娃", updated["prompt"])

    def test_edit_requires_current_session_receipt_and_keeps_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, codex_home = self._environment(root)
            primary = _lock_identity(home)
            previous = codex_home / "generated_images" / "previous.png"
            previous.parent.mkdir(parents=True)
            previous.write_bytes(tiny_png(metadata=b"previous"))
            session_id = "edit-session"
            turn_id = "edit-turn"
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(home),
                    "CODEX_HOME": str(codex_home),
                },
                clear=True,
            ):
                CodexImageReceiptStore().record(
                    session_id=session_id,
                    tool_use_id="previous-image-call",
                    paths=(previous,),
                )
                PhotoMomentStore(lock_timeout=0.25).record_image_result(
                    profile_id="companion",
                    session_id=session_id,
                    paths=(previous,),
                    is_companion=True,
                    identity_version=1,
                )

            payload = {
                "hook_event_name": "PreToolUse",
                "session_id": session_id,
                "turn_id": turn_id,
                "tool_use_id": "edit-photo-call",
                "tool_name": "image_gen__imagegen",
                "tool_input": {
                    "prompt": self._prompt_with_envelope(
                        home=home,
                        session_id=session_id,
                        turn_id=turn_id,
                        mode="edit_previous",
                        prompt="只把上一张照片里的眼镜换成黑框",
                    ),
                    "referenced_image_paths": [str(previous)],
                    "num_last_images_to_include": 1,
                },
            }
            completed = _run_hook(PRE_TOOL_HOOK, payload, environment)

            self.assertEqual(completed.returncode, 0)
            updated = json.loads(completed.stdout)["hookSpecificOutput"]["updatedInput"]
            self.assertEqual(
                updated["referenced_image_paths"],
                [str(primary.path), str(previous)],
            )
            self.assertNotIn("num_last_images_to_include", updated)
            self.assertIn("只修改用户明确提出的部分", updated["prompt"])

            payload["session_id"] = "another-session"
            denied = _run_hook(PRE_TOOL_HOOK, payload, environment)
            decision = json.loads(denied.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertNotIn(str(previous), json.dumps(decision, ensure_ascii=False))

    def test_stale_companion_marker_cannot_authorize_overwritten_generic_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, codex_home = self._environment(root)
            _lock_identity(home)
            image = codex_home / "generated_images" / "reused.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(tiny_png(metadata=b"companion"))
            session_id = "reused-path-session"
            turn_id = "reused-path-turn"
            with patch.dict(
                os.environ,
                {
                    "COMPANION_HOME": str(home),
                    "CODEX_HOME": str(codex_home),
                },
                clear=True,
            ):
                receipts = CodexImageReceiptStore()
                receipts.record(
                    session_id=session_id,
                    tool_use_id="companion-call",
                    paths=(image,),
                )
                PhotoMomentStore().record_image_result(
                    profile_id="companion",
                    session_id=session_id,
                    paths=(image,),
                    is_companion=True,
                    identity_version=1,
                )
                image.write_bytes(tiny_png(metadata=b"generic-overwrite"))
                receipts.record(
                    session_id=session_id,
                    tool_use_id="generic-call",
                    paths=(image,),
                )

            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": "edit-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": self._prompt_with_envelope(
                            home=home,
                            session_id=session_id,
                            turn_id=turn_id,
                            mode="edit_previous",
                            prompt="把上一张改成短发",
                        ),
                        "referenced_image_paths": [str(image)],
                    },
                },
                environment,
            )

            decision = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertNotIn(str(image), json.dumps(decision, ensure_ascii=False))

    def test_successful_post_tool_commits_same_moment_and_returns_caption_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, codex_home = self._environment(root)
            _lock_identity(home)
            session_id = "caption-session"
            turn_id = "caption-turn"
            tool_use_id = "caption-image-call"
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                CompanionScopeStore().bind(session_id)
            pre = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": tool_use_id,
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": self._prompt_with_envelope(
                            home=home,
                            session_id=session_id,
                            turn_id=turn_id,
                            mode="new",
                            prompt="自然生活感的窗边自拍",
                        )
                    },
                },
                environment,
            )
            self.assertEqual(
                json.loads(pre.stdout)["hookSpecificOutput"]["permissionDecision"],
                "allow",
            )
            generated = codex_home / "generated_images" / "result.png"
            generated.parent.mkdir(parents=True, exist_ok=True)
            generated.write_bytes(tiny_png(metadata=b"result"))

            post = _run_hook(
                POST_TOOL_HOOK,
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": tool_use_id,
                    "tool_name": "image_gen__imagegen",
                    "tool_input": json.loads(pre.stdout)["hookSpecificOutput"]["updatedInput"],
                    "tool_response": {"output_hint": str(generated)},
                },
                environment,
            )

            self.assertEqual(post.returncode, 0)
            self.assertEqual(post.stderr, "")
            context = json.loads(post.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("窗边", context)
            self.assertIn("灵动和逗弄感", context)
            self.assertIn("话留一半", context)
            self.assertNotIn(str(generated), context)
            self.assertNotIn(session_id, context)
            self.assertNotIn(tool_use_id, context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                recent = PhotoMomentStore().recent(
                    profile_id="companion",
                    identity_version=1,
                )
                continuation = PendingPhotoContextStore().peek(
                    profile_id="companion",
                    session_id=session_id,
                )
            self.assertEqual(recent, (_moment(),))
            self.assertIsNotNone(continuation)
            self.assertEqual(continuation.source, "photo_flow")

            duplicate = _run_hook(
                POST_TOOL_HOOK,
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": tool_use_id,
                    "tool_name": "image_gen__imagegen",
                    "tool_response": {"output_hint": str(generated)},
                },
                environment,
            )
            self.assertEqual(duplicate.stdout, "")
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                moments = PhotoMomentStore()
                self.assertTrue(
                    moments.latest_result_is_companion(
                        profile_id="companion",
                        session_id=session_id,
                        identity_version=1,
                    )
                )

            failed_generic = _run_hook(
                POST_TOOL_HOOK,
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    "turn_id": "generic-failed-turn",
                    "tool_use_id": "generic-failed-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_response": {"error": "provider failed"},
                },
                environment,
            )
            self.assertEqual(failed_generic.stdout, "")
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                self.assertTrue(
                    PhotoMomentStore().latest_result_is_companion(
                        profile_id="companion",
                        session_id=session_id,
                        identity_version=1,
                    )
                )

            generic = codex_home / "generated_images" / "generic.png"
            generic.write_bytes(tiny_png(metadata=b"generic"))
            successful_generic = _run_hook(
                POST_TOOL_HOOK,
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    "turn_id": "generic-turn",
                    "tool_use_id": "generic-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_response": {"output_hint": str(generic)},
                },
                environment,
            )
            self.assertEqual(successful_generic.stdout, "")
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                self.assertFalse(
                    PhotoMomentStore().latest_result_is_companion(
                        profile_id="companion",
                        session_id=session_id,
                        identity_version=1,
                    )
                )

    def test_daily_look_is_applied_and_confirmed_only_after_real_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, codex_home = self._environment(root)
            primary = _lock_identity(home)
            session_id = "daily-look-session"
            turn_id = "daily-look-turn"
            tool_use_id = "daily-look-call"
            token = make_turn_token(
                session_id=session_id,
                turn_id=turn_id,
                mode="new",
                hook_bundle_digest=inspect_hook_bundle(PROJECT_ROOT).hook_bundle_digest,
            )
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                look_store = DailyLookStore()
                look = look_store.ensure_today(
                    profile_id="companion",
                    style_anchor="温柔自然，舒适干净",
                )
                assert look is not None
                PhotoMomentStore().issue_turn(
                    profile_id="companion",
                    session_id=session_id,
                    turn_id=turn_id,
                    mode="new",
                    turn_token=token,
                )
            prompt = "自然生活感的半身自拍" + encode_photo_envelope(
                token,
                _moment(),
                daily_look=DailyLookDirective.from_dict(
                    {
                        "action": "use_daily",
                        "look_id": look.look_id,
                        "proposal": None,
                    }
                ),
            )

            pre = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": tool_use_id,
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {"prompt": prompt},
                },
                environment,
            )

            pre_output = json.loads(pre.stdout)["hookSpecificOutput"]
            self.assertEqual(pre_output["permissionDecision"], "allow")
            updated = pre_output["updatedInput"]
            self.assertEqual(updated["referenced_image_paths"], [str(primary.path)])
            self.assertIn(look.title, updated["prompt"])
            self.assertIn("只固定今天的穿搭锚点", updated["prompt"])
            self.assertNotIn("COMPANION_KIT_PHOTO_V4", updated["prompt"])
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                self.assertEqual(
                    DailyLookStore().current(profile_id="companion").status,
                    "planned",
                )

            generated = codex_home / "generated_images" / "daily-look.png"
            generated.parent.mkdir(parents=True, exist_ok=True)
            generated.write_bytes(tiny_png(metadata=b"daily-look"))
            post = _run_hook(
                POST_TOOL_HOOK,
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": tool_use_id,
                    "tool_name": "image_gen__imagegen",
                    "tool_response": {"output_hint": str(generated)},
                },
                environment,
            )

            caption_context = json.loads(post.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn(look.title, caption_context)
            self.assertIn("画面里确实可见", caption_context)
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                self.assertEqual(
                    DailyLookStore().current(profile_id="companion").status,
                    "confirmed",
                )

    def test_failed_daily_look_photo_discards_pending_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            session_id = "failed-look-session"
            turn_id = "failed-look-turn"
            tool_use_id = "failed-look-call"
            token = make_turn_token(
                session_id=session_id,
                turn_id=turn_id,
                mode="new",
                hook_bundle_digest=inspect_hook_bundle(PROJECT_ROOT).hook_bundle_digest,
            )
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                look = DailyLookStore().ensure_today(
                    profile_id="companion",
                    style_anchor="自然松弛",
                )
                assert look is not None
                PhotoMomentStore().issue_turn(
                    profile_id="companion",
                    session_id=session_id,
                    turn_id=turn_id,
                    mode="new",
                    turn_token=token,
                )
            pre = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": tool_use_id,
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": "自然自拍"
                        + encode_photo_envelope(
                            token,
                            _moment(),
                            daily_look=DailyLookDirective.from_dict(
                                {
                                    "action": "use_daily",
                                    "look_id": look.look_id,
                                    "proposal": None,
                                }
                            ),
                        )
                    },
                },
                environment,
            )
            self.assertEqual(
                json.loads(pre.stdout)["hookSpecificOutput"]["permissionDecision"],
                "allow",
            )
            failed = _run_hook(
                POST_TOOL_HOOK,
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": tool_use_id,
                    "tool_name": "image_gen__imagegen",
                    "tool_response": {"error": "provider failed"},
                },
                environment,
            )
            self.assertEqual(failed.stdout, "")
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                store = DailyLookStore()
                self.assertEqual(store.current(profile_id="companion").status, "planned")
                self.assertIsNone(
                    store.peek_for_photo(
                        profile_id="companion",
                        session_id=session_id,
                        tool_use_id=tool_use_id,
                    )
                )

    def test_edit_photo_rejects_a_daily_replace_directive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            session_id = "edit-look-session"
            turn_id = "edit-look-turn"
            token = make_turn_token(
                session_id=session_id,
                turn_id=turn_id,
                mode="edit_previous",
                hook_bundle_digest=inspect_hook_bundle(PROJECT_ROOT).hook_bundle_digest,
            )
            with patch.dict(
                os.environ,
                {"COMPANION_HOME": str(home)},
                clear=True,
            ):
                look = DailyLookStore().ensure_today(
                    profile_id="companion",
                    style_anchor="利落",
                )
                assert look is not None
                PhotoMomentStore().issue_turn(
                    profile_id="companion",
                    session_id=session_id,
                    turn_id=turn_id,
                    mode="edit_previous",
                    turn_token=token,
                )
            directive = DailyLookDirective.from_dict(
                {
                    "action": "replace_daily",
                    "look_id": look.look_id,
                    "proposal": DailyLookProposal.from_dict(
                        {
                            "title": "不该生效",
                            "palette": "黑白",
                            "silhouette": "直线",
                            "hero_piece": "白衬衫",
                            "accent": "腕表",
                        }
                    ).to_dict(),
                }
            )
            completed = _run_hook(
                PRE_TOOL_HOOK,
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "tool_use_id": "edit-look-call",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {
                        "prompt": "只改上一张的发型"
                        + encode_photo_envelope(
                            token,
                            _moment(mode="edit_previous"),
                            daily_look=directive,
                        ),
                        "referenced_image_paths": [str(root / "missing.png")],
                    },
                },
                environment,
            )
            decision = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertIn("编辑上一张", decision["permissionDecisionReason"])

    def test_pre_tool_identity_lock_contention_denies_before_hook_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, _ = self._environment(root)
            _lock_identity(home)
            session_id = "locked-assets-session"
            turn_id = "locked-assets-turn"
            prompt = self._prompt_with_envelope(
                home=home,
                session_id=session_id,
                turn_id=turn_id,
                mode="new",
                prompt="自然自拍",
            )
            lock_path = home / "private" / "images" / ".assets.lock"

            with exclusive_file_lock(lock_path, timeout=0.25):
                completed = _run_hook(
                    PRE_TOOL_HOOK,
                    {
                        "hook_event_name": "PreToolUse",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "tool_use_id": "locked-assets-call",
                        "tool_name": "image_gen__imagegen",
                        "tool_input": {"prompt": prompt},
                    },
                    environment,
                    timeout=2.5,
                )

            decision = json.loads(completed.stdout)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertIn("身份参考", decision["permissionDecisionReason"])

    def test_post_tool_receipt_lock_contention_returns_before_hook_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            environment, home, codex_home = self._environment(root)
            initialize_profile(
                skill_root=SKILL_ROOT,
                template_id="warm_healer",
                display_name="小禾",
                output=home / "profiles" / "default.toml",
            )
            generated = codex_home / "generated_images" / "locked.png"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(tiny_png(metadata=b"locked"))
            receipt_root = home / "private" / "codex-image-receipts"
            receipt_root.mkdir(parents=True)
            lock_path = receipt_root / ".receipts.lock"

            with exclusive_file_lock(lock_path, timeout=0.25):
                completed = _run_hook(
                    POST_TOOL_HOOK,
                    {
                        "hook_event_name": "PostToolUse",
                        "session_id": "locked-receipt-session",
                        "turn_id": "locked-receipt-turn",
                        "tool_use_id": "locked-receipt-call",
                        "tool_name": "image_gen__imagegen",
                        "tool_response": {"output_hint": str(generated)},
                    },
                    environment,
                    timeout=2.5,
                )

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
