from __future__ import annotations

import struct
import zlib


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def tiny_png(
    *,
    metadata: bytes | None = None,
    rgba: bytes = b"\x20\x40\x60\xff",
) -> bytes:
    """生成一个 1×1 RGBA PNG；只用于测试，不是项目人物素材。"""

    if len(rgba) != 4:
        raise ValueError("rgba 必须正好包含 4 个字节")
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw_pixel = b"\x00" + rgba
    chunks = [_chunk(b"IHDR", header)]
    if metadata is not None:
        chunks.append(_chunk(b"tEXt", b"note\x00" + metadata))
    chunks.extend((_chunk(b"IDAT", zlib.compress(raw_pixel)), _chunk(b"IEND", b"")))
    return signature + b"".join(chunks)
