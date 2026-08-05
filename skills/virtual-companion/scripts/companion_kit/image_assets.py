from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
from typing import Callable, Iterable
import uuid
import zlib

from .file_lock import InterprocessLockError, exclusive_file_lock


class ImageAssetError(ValueError):
    """图片资产无法在本地安全保存或解析。"""


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_OPAQUE_ID_RE = re.compile(r"^(?:cand|ref|art)_[a-f0-9]{16,32}$")
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_PIXELS = 16_000_000
_SELECTED_KEYS = {
    "schema_version",
    "reference_id",
    "profile_id",
    "identity_version",
    "sha256",
    "mime_type",
    "width",
    "height",
    "source",
    "created_at",
}
_PENDING_KEYS = (_SELECTED_KEYS - {"reference_id"}) | {
    "candidate_id",
    "task_scope_digest",
}
_ARTIFACT_KEYS = (_SELECTED_KEYS - {"reference_id"}) | {
    "artifact_id",
    "task_scope_digest",
}


@dataclass(frozen=True)
class CandidateAsset:
    candidate_id: str
    profile_id: str
    identity_version: int
    width: int
    height: int
    path: Path


@dataclass(frozen=True)
class ReferenceAsset:
    reference_id: str
    profile_id: str
    identity_version: int
    width: int
    height: int
    path: Path


@dataclass(frozen=True)
class GeneratedArtifact:
    artifact_id: str
    profile_id: str
    identity_version: int
    width: int
    height: int
    path: Path


def _safe_absolute_path(raw: str | Path) -> Path:
    raw_path = Path(raw).expanduser()
    if ".." in raw_path.parts:
        raise ImageAssetError("图片资产路径不能包含 ..")
    path = Path(os.path.abspath(raw_path))
    if sys.platform == "darwin":
        filesystem_root = Path(path.anchor)
        for alias, target in (
            (filesystem_root / "var", filesystem_root / "private" / "var"),
            (filesystem_root / "tmp", filesystem_root / "private" / "tmp"),
        ):
            try:
                path = target / path.relative_to(alias)
                break
            except ValueError:
                continue

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ImageAssetError(f"无法检查图片资产路径：{exc}") from exc
        if stat.S_ISLNK(mode):
            raise ImageAssetError("图片资产路径不能经过符号链接")
    return path


def _codex_generated_images_root() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    codex_home = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return _safe_absolute_path(codex_home / "generated_images")


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_absolute_path(path)
    if not path.is_dir():
        raise ImageAssetError("图片资产目录必须是普通目录")
    if os.name != "nt":
        path.chmod(0o700)


def _validate_profile(profile_id: str, identity_version: int) -> None:
    if not _PROFILE_ID_RE.fullmatch(str(profile_id or "")):
        raise ImageAssetError("profile_id 格式无效")
    if (
        not isinstance(identity_version, int)
        or isinstance(identity_version, bool)
        or identity_version < 1
    ):
        raise ImageAssetError("identity_version 必须是正整数")


def _task_digest(task_scope: str) -> str:
    normalized = str(task_scope or "")
    if (
        not normalized
        or len(normalized) > 256
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ImageAssetError("当前任务作用域无效")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def sanitize_png(image_bytes: bytes) -> tuple[bytes, int, int]:
    """严格校验并只保留 PNG 的像素必需块，去除文本与 EXIF 等元数据。"""

    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise ImageAssetError("图片结果为空")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise ImageAssetError("图片结果超过本地安全上限")
    if not image_bytes.startswith(_PNG_SIGNATURE):
        raise ImageAssetError("图片结果必须是 PNG")

    offset = len(_PNG_SIGNATURE)
    header: bytes | None = None
    idat_parts: list[bytes] = []
    saw_end = False
    while offset < len(image_bytes):
        if offset + 12 > len(image_bytes):
            raise ImageAssetError("PNG 数据不完整")
        length = struct.unpack(">I", image_bytes[offset : offset + 4])[0]
        if length > _MAX_IMAGE_BYTES or offset + 12 + length > len(image_bytes):
            raise ImageAssetError("PNG 分块长度无效")
        kind = image_bytes[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload = image_bytes[payload_start : payload_start + length]
        expected_crc = struct.unpack(
            ">I", image_bytes[payload_start + length : offset + 12 + length]
        )[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ImageAssetError("PNG 校验失败")
        offset += 12 + length

        if kind == b"IHDR":
            if header is not None or idat_parts or length != 13:
                raise ImageAssetError("PNG 头部无效")
            header = payload
        elif kind == b"IDAT":
            if header is None or saw_end:
                raise ImageAssetError("PNG 像素块顺序无效")
            idat_parts.append(payload)
        elif kind == b"IEND":
            if length != 0 or header is None or not idat_parts:
                raise ImageAssetError("PNG 结束块无效")
            saw_end = True
            if offset != len(image_bytes):
                raise ImageAssetError("PNG 末尾包含额外数据")
            break
        elif kind[:1].isupper():
            raise ImageAssetError("PNG 包含不受支持的关键分块")

    if header is None or not idat_parts or not saw_end:
        raise ImageAssetError("PNG 缺少必要分块")

    width, height, bit_depth, colour_type, compression, filtering, interlace = (
        struct.unpack(">IIBBBBB", header)
    )
    channels = {2: 3, 6: 4}.get(colour_type)
    if (
        width < 1
        or height < 1
        or width * height > _MAX_PIXELS
        or bit_depth != 8
        or channels is None
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        raise ImageAssetError("PNG 像素格式不受支持")

    expected_size = height * (1 + width * channels)
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(b"".join(idat_parts), expected_size + 1)
        raw += decompressor.flush(expected_size + 1 - len(raw))
    except zlib.error as exc:
        raise ImageAssetError("PNG 像素数据无法解码") from exc
    if (
        len(raw) != expected_size
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        raise ImageAssetError("PNG 解码大小无效")
    stride = 1 + width * channels
    if any(raw[row * stride] > 4 for row in range(height)):
        raise ImageAssetError("PNG 使用了无效滤镜")

    sanitized = (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", b"".join(idat_parts))
        + _chunk(b"IEND", b"")
    )
    return sanitized, width, height


def _atomic_write(path: Path, payload: bytes) -> None:
    _private_directory(path.parent)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=".companion-asset-",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            temporary = Path(handle.name)
        temporary.replace(path)
        if os.name != "nt":
            path.chmod(0o600)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_manifest(path: Path, expected_keys: set[str]) -> dict[str, object]:
    _safe_absolute_path(path)
    if path.is_symlink() or not path.is_file():
        raise ImageAssetError("图片资产清单不存在")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImageAssetError("图片资产清单无效") from exc
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise ImageAssetError("图片资产清单结构无效")
    return raw


class ImageAssetStore:
    """仅保存一个身份参考、一个候选槽和短暂的当前任务成图。"""

    def __init__(
        self,
        root: str | Path,
        *,
        forbidden_roots: Iterable[str | Path] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = _safe_absolute_path(root)
        for raw_forbidden in forbidden_roots:
            forbidden = _safe_absolute_path(raw_forbidden)
            if self.root == forbidden or forbidden in self.root.parents:
                raise ImageAssetError("图片资产必须保存在发行项目之外")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock_path = self.root / ".assets.lock"

    def _profile_root(self, profile_id: str, identity_version: int) -> Path:
        _validate_profile(profile_id, identity_version)
        return self.root / "identities" / profile_id / f"v{identity_version}"

    def _locked(self):
        _private_directory(self.root)
        _safe_absolute_path(self._lock_path)
        return exclusive_file_lock(self._lock_path)

    def _created_at(self) -> str:
        now = self._clock()
        if now.tzinfo is None:
            raise ImageAssetError("图片资产时间必须包含时区")
        return now.astimezone(UTC).isoformat()

    def store_candidate(
        self,
        *,
        profile_id: str,
        identity_version: int,
        image_bytes: bytes,
        task_scope: str,
        source: str = "openai_image_api",
    ) -> CandidateAsset:
        if source not in {"openai_image_api", "codex_native"}:
            raise ImageAssetError("候选原型来源无效")
        sanitized, width, height = sanitize_png(image_bytes)
        digest = _task_digest(task_scope)
        candidate_id = f"cand_{uuid.uuid4().hex[:24]}"
        profile_root = self._profile_root(profile_id, identity_version)
        image_path = profile_root / "pending.png"
        manifest_path = profile_root / "pending.json"
        manifest = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "profile_id": profile_id,
            "identity_version": identity_version,
            "sha256": sha256(sanitized).hexdigest(),
            "mime_type": "image/png",
            "width": width,
            "height": height,
            "source": source,
            "created_at": self._created_at(),
            "task_scope_digest": digest,
        }
        try:
            with self._locked():
                _private_directory(profile_root)
                image_path.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                _atomic_write(image_path, sanitized)
                _atomic_write(manifest_path, _json_bytes(manifest))
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError(f"无法保存候选原型：{exc}") from exc
        return CandidateAsset(
            candidate_id=candidate_id,
            profile_id=profile_id,
            identity_version=identity_version,
            width=width,
            height=height,
            path=image_path,
        )

    def store_candidate_file(
        self,
        *,
        profile_id: str,
        identity_version: int,
        source_path: str | Path,
        task_scope: str,
    ) -> CandidateAsset:
        """导入 Codex 当前任务已经真实生成或取得的一张 PNG 候选图。"""

        path = _safe_absolute_path(source_path)
        generated_root = _codex_generated_images_root()
        try:
            path.relative_to(generated_root)
        except ValueError as exc:
            raise ImageAssetError(
                "候选原型必须来自 Codex 当前任务的 generated_images 目录"
            ) from exc
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ImageAssetError("候选原型文件不存在或无法读取") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ImageAssetError("候选原型必须是普通图片文件，不能是符号链接")
        try:
            size = path.stat().st_size
            if size < 1 or size > _MAX_IMAGE_BYTES:
                raise ImageAssetError("候选原型文件大小超出安全范围")
            image_bytes = path.read_bytes()
        except ImageAssetError:
            raise
        except OSError as exc:
            raise ImageAssetError("候选原型文件无法读取") from exc
        try:
            return self.store_candidate(
                profile_id=profile_id,
                identity_version=identity_version,
                image_bytes=image_bytes,
                task_scope=task_scope,
                source="codex_native",
            )
        except ImageAssetError as exc:
            if not image_bytes.startswith(_PNG_SIGNATURE):
                raise ImageAssetError(
                    "候选原型需要是 PNG；其他格式请先由 Codex 内置图片能力转换为候选 PNG"
                ) from exc
            raise

    def confirm_candidate(
        self,
        *,
        candidate_id: str,
        profile_id: str,
        identity_version: int,
        task_scope: str,
    ) -> ReferenceAsset:
        if not _OPAQUE_ID_RE.fullmatch(str(candidate_id or "")) or not str(
            candidate_id
        ).startswith("cand_"):
            raise ImageAssetError("candidate_id 格式无效")
        digest = _task_digest(task_scope)
        profile_root = self._profile_root(profile_id, identity_version)
        pending_image = profile_root / "pending.png"
        pending_manifest = profile_root / "pending.json"
        selected_image = profile_root / "selected.png"
        selected_manifest = profile_root / "selected.json"
        try:
            with self._locked():
                manifest = _load_manifest(pending_manifest, _PENDING_KEYS)
                if (
                    manifest["candidate_id"] != candidate_id
                    or manifest["profile_id"] != profile_id
                    or manifest["identity_version"] != identity_version
                    or manifest["task_scope_digest"] != digest
                ):
                    raise ImageAssetError("候选原型与当前任务或身份版本不匹配")
                if pending_image.is_symlink() or not pending_image.is_file():
                    raise ImageAssetError("候选原型图片不存在")
                sanitized, width, height = sanitize_png(pending_image.read_bytes())
                content_hash = sha256(sanitized).hexdigest()
                if content_hash != manifest["sha256"]:
                    raise ImageAssetError("候选原型内容已变化")
                reference_id = f"ref_{content_hash[:24]}"
                selected = {
                    key: value
                    for key, value in manifest.items()
                    if key not in {"candidate_id", "task_scope_digest"}
                }
                selected["reference_id"] = reference_id
                _atomic_write(selected_image, sanitized)
                _atomic_write(selected_manifest, _json_bytes(selected))
                pending_image.unlink(missing_ok=True)
                pending_manifest.unlink(missing_ok=True)
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError(f"无法确认身份参考：{exc}") from exc
        return ReferenceAsset(
            reference_id=reference_id,
            profile_id=profile_id,
            identity_version=identity_version,
            width=width,
            height=height,
            path=selected_image,
        )

    def resolve_candidate(
        self,
        *,
        candidate_id: str,
        profile_id: str,
        identity_version: int,
        task_scope: str,
    ) -> Path:
        """校验候选仍属于当前会话，但不把它提升为身份参考。"""

        try:
            with self._locked():
                image_path, _ = self._verified_candidate_locked(
                    candidate_id=candidate_id,
                    profile_id=profile_id,
                    identity_version=identity_version,
                    task_scope=task_scope,
                )
                return image_path
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError("无法读取候选原型图片") from exc

    def _verified_candidate_locked(
        self,
        *,
        candidate_id: str,
        profile_id: str,
        identity_version: int,
        task_scope: str,
    ) -> tuple[Path, bytes]:
        if not _OPAQUE_ID_RE.fullmatch(str(candidate_id or "")) or not str(
            candidate_id
        ).startswith("cand_"):
            raise ImageAssetError("candidate_id 格式无效")
        profile_root = self._profile_root(profile_id, identity_version)
        image_path = profile_root / "pending.png"
        manifest_path = profile_root / "pending.json"
        manifest = _load_manifest(manifest_path, _PENDING_KEYS)
        if (
            manifest["candidate_id"] != candidate_id
            or manifest["profile_id"] != profile_id
            or manifest["identity_version"] != identity_version
            or manifest["task_scope_digest"] != _task_digest(task_scope)
        ):
            raise ImageAssetError("候选原型与当前会话或身份版本不匹配")
        if image_path.is_symlink() or not image_path.is_file():
            raise ImageAssetError("候选原型图片不存在")
        sanitized, width, height = sanitize_png(image_path.read_bytes())
        if (
            sha256(sanitized).hexdigest() != manifest["sha256"]
            or width != manifest["width"]
            or height != manifest["height"]
        ):
            raise ImageAssetError("候选原型内容已变化")
        return image_path, sanitized

    def read_candidate_bytes(
        self,
        *,
        candidate_id: str,
        profile_id: str,
        identity_version: int,
        task_scope: str,
    ) -> bytes:
        """在资产锁内校验并复制候选内容，供一次性交付快照使用。"""

        try:
            with self._locked():
                _, image_bytes = self._verified_candidate_locked(
                    candidate_id=candidate_id,
                    profile_id=profile_id,
                    identity_version=identity_version,
                    task_scope=task_scope,
                )
                return image_bytes
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError("无法读取候选原型图片") from exc

    def resolve_reference(
        self,
        *,
        reference_id: str,
        profile_id: str,
        identity_version: int,
    ) -> Path:
        try:
            with self._locked():
                image_path, _ = self._verified_reference_locked(
                    reference_id=reference_id,
                    profile_id=profile_id,
                    identity_version=identity_version,
                )
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError(f"无法解析身份参考：{exc}") from exc
        return image_path

    def _verified_reference_locked(
        self,
        *,
        reference_id: str,
        profile_id: str,
        identity_version: int,
    ) -> tuple[Path, bytes]:
        if not _OPAQUE_ID_RE.fullmatch(str(reference_id or "")) or not str(
            reference_id
        ).startswith("ref_"):
            raise ImageAssetError("reference_id 格式无效")
        profile_root = self._profile_root(profile_id, identity_version)
        image_path = profile_root / "selected.png"
        manifest_path = profile_root / "selected.json"
        manifest = _load_manifest(manifest_path, _SELECTED_KEYS)
        if (
            manifest["reference_id"] != reference_id
            or manifest["profile_id"] != profile_id
            or manifest["identity_version"] != identity_version
        ):
            raise ImageAssetError("身份参考与当前配置不匹配")
        if image_path.is_symlink() or not image_path.is_file():
            raise ImageAssetError("身份参考图片不存在")
        image_bytes = image_path.read_bytes()
        sanitized, width, height = sanitize_png(image_bytes)
        if (
            sha256(sanitized).hexdigest() != manifest["sha256"]
            or width != manifest["width"]
            or height != manifest["height"]
        ):
            raise ImageAssetError("身份参考内容已变化")
        return image_path, image_bytes

    def read_reference_bytes(
        self,
        *,
        reference_id: str,
        profile_id: str,
        identity_version: int,
    ) -> bytes:
        try:
            with self._locked():
                _, image_bytes = self._verified_reference_locked(
                    reference_id=reference_id,
                    profile_id=profile_id,
                    identity_version=identity_version,
                )
                return image_bytes
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError("无法读取身份参考图片") from exc

    def store_artifact(
        self,
        *,
        profile_id: str,
        identity_version: int,
        image_bytes: bytes,
        task_scope: str,
    ) -> GeneratedArtifact:
        _validate_profile(profile_id, identity_version)
        sanitized, width, height = sanitize_png(image_bytes)
        artifact_id = f"art_{uuid.uuid4().hex[:24]}"
        artifact_root = self.root / "runtime" / artifact_id
        image_path = artifact_root / "result.png"
        manifest_path = artifact_root / "result.json"
        manifest = {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "profile_id": profile_id,
            "identity_version": identity_version,
            "sha256": sha256(sanitized).hexdigest(),
            "mime_type": "image/png",
            "width": width,
            "height": height,
            "source": "openai_image_api",
            "created_at": self._created_at(),
            "task_scope_digest": _task_digest(task_scope),
        }
        try:
            with self._locked():
                _private_directory(artifact_root)
                _atomic_write(image_path, sanitized)
                _atomic_write(manifest_path, _json_bytes(manifest))
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError(f"无法保存当前任务成图：{exc}") from exc
        return GeneratedArtifact(
            artifact_id=artifact_id,
            profile_id=profile_id,
            identity_version=identity_version,
            width=width,
            height=height,
            path=image_path,
        )

    def resolve_artifact(
        self,
        *,
        artifact_id: str,
        profile_id: str,
        identity_version: int,
        task_scope: str,
    ) -> Path:
        """在交给宿主前重新校验短期成图与当前会话绑定。"""

        if not _OPAQUE_ID_RE.fullmatch(str(artifact_id or "")) or not str(
            artifact_id
        ).startswith("art_"):
            raise ImageAssetError("artifact_id 格式无效")
        artifact_root = self.root / "runtime" / artifact_id
        image_path = artifact_root / "result.png"
        manifest_path = artifact_root / "result.json"
        try:
            with self._locked():
                manifest = _load_manifest(manifest_path, _ARTIFACT_KEYS)
                if (
                    manifest["artifact_id"] != artifact_id
                    or manifest["profile_id"] != profile_id
                    or manifest["identity_version"] != identity_version
                    or manifest["task_scope_digest"] != _task_digest(task_scope)
                ):
                    raise ImageAssetError("当前会话不能接管这个图片")
                if image_path.is_symlink() or not image_path.is_file():
                    raise ImageAssetError("当前会话图片不存在")
                sanitized, width, height = sanitize_png(image_path.read_bytes())
                if (
                    sha256(sanitized).hexdigest() != manifest["sha256"]
                    or width != manifest["width"]
                    or height != manifest["height"]
                ):
                    raise ImageAssetError("当前会话图片内容已变化")
                return image_path
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError("无法读取当前会话图片") from exc

    def prune_runtime(self, *, max_age: timedelta = timedelta(hours=1)) -> int:
        """清理无人确认投递的过期成图；不触碰候选或固定参考。"""

        if max_age <= timedelta(0) or max_age > timedelta(days=1):
            raise ImageAssetError("短期成图保留时间必须在 1 天以内")
        now = self._clock()
        if now.tzinfo is None:
            raise ImageAssetError("图片资产时间必须包含时区")
        now = now.astimezone(UTC)
        runtime_root = self.root / "runtime"
        removed = 0
        try:
            with self._locked():
                if not runtime_root.exists():
                    return 0
                if runtime_root.is_symlink() or not runtime_root.is_dir():
                    raise ImageAssetError("短期成图目录不安全")
                for artifact_root in runtime_root.iterdir():
                    if (
                        artifact_root.is_symlink()
                        or not artifact_root.is_dir()
                        or not _OPAQUE_ID_RE.fullmatch(artifact_root.name)
                        or not artifact_root.name.startswith("art_")
                    ):
                        raise ImageAssetError("短期成图目录包含未知内容")
                    children = list(artifact_root.iterdir())
                    for child in children:
                        if child.name.startswith(".companion-asset-"):
                            if child.is_symlink() or not child.is_file():
                                raise ImageAssetError("短期成图临时文件不安全")
                            child.unlink()
                    children = list(artifact_root.iterdir())
                    child_names = {child.name for child in children}
                    expected_names = {"result.png", "result.json"}
                    if not child_names.issubset(expected_names):
                        raise ImageAssetError("短期成图目录包含未知文件")
                    if child_names != expected_names:
                        # 写图片与写清单之间进程可能崩溃。目录名和文件名均由本流程
                        # 严格限定，取得全局资产锁后可安全删除这个不可投递半成品。
                        for child in children:
                            if child.is_symlink() or not child.is_file():
                                raise ImageAssetError("短期成图文件不安全")
                            child.unlink()
                        artifact_root.rmdir()
                        removed += 1
                        continue
                    manifest = _load_manifest(
                        artifact_root / "result.json",
                        _ARTIFACT_KEYS,
                    )
                    if manifest["artifact_id"] != artifact_root.name:
                        raise ImageAssetError("短期成图清单与目录不匹配")
                    try:
                        created_at = datetime.fromisoformat(str(manifest["created_at"]))
                    except ValueError as exc:
                        raise ImageAssetError("短期成图时间无效") from exc
                    if created_at.tzinfo is None:
                        raise ImageAssetError("短期成图时间缺少时区")
                    if now - created_at.astimezone(UTC) <= max_age:
                        continue
                    for child in artifact_root.iterdir():
                        if child.is_symlink() or not child.is_file():
                            raise ImageAssetError("短期成图文件不安全")
                        child.unlink()
                    artifact_root.rmdir()
                    removed += 1
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError(f"无法清理过期成图：{exc}") from exc
        return removed

    def finish_delivery(self, *, artifact_id: str, task_scope: str) -> None:
        if not _OPAQUE_ID_RE.fullmatch(str(artifact_id or "")) or not str(
            artifact_id
        ).startswith("art_"):
            raise ImageAssetError("artifact_id 格式无效")
        artifact_root = self.root / "runtime" / artifact_id
        manifest_path = artifact_root / "result.json"
        try:
            with self._locked():
                manifest = _load_manifest(manifest_path, _ARTIFACT_KEYS)
                if (
                    manifest["artifact_id"] != artifact_id
                    or manifest["task_scope_digest"] != _task_digest(task_scope)
                ):
                    raise ImageAssetError("当前任务不能完成这个图片投递")
                for name in ("result.png", "result.json"):
                    target = artifact_root / name
                    if target.is_symlink():
                        raise ImageAssetError("当前任务成图路径不安全")
                    target.unlink(missing_ok=True)
                artifact_root.rmdir()
        except (OSError, InterprocessLockError) as exc:
            raise ImageAssetError(f"无法清理当前任务成图：{exc}") from exc
