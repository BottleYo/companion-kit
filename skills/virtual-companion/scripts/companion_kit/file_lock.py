from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import time
from typing import Iterator


class InterprocessLockError(OSError):
    """无法在限定时间内取得本地跨进程文件锁。"""


@contextmanager
def exclusive_file_lock(
    path: str | Path,
    *,
    timeout: float = 5.0,
) -> Iterator[None]:
    """用一个私有普通文件协调同机进程；调用方负责先验证父目录。"""

    lock_path = Path(path)
    if timeout <= 0:
        raise InterprocessLockError("文件锁等待时间必须大于零")
    if lock_path.is_symlink():
        raise InterprocessLockError("锁文件不能是符号链接")

    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise InterprocessLockError(f"无法打开锁文件：{exc}") from exc

    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InterprocessLockError("锁目标必须是普通文件")
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + timeout

        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise InterprocessLockError("等待本地配置锁超时") from exc
                    time.sleep(0.02)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise InterprocessLockError("等待本地配置锁超时") from exc
                    time.sleep(0.02)

        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
