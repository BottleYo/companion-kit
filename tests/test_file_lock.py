import multiprocessing
from pathlib import Path
import tempfile
import unittest

from companion_kit.file_lock import InterprocessLockError, exclusive_file_lock


def hold_lock(path: str, ready, release) -> None:
    with exclusive_file_lock(path, timeout=2):
        ready.put(True)
        release.get(timeout=5)


class FileLockTests(unittest.TestCase):
    def test_lock_coordinates_separate_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp).resolve() / "profile.lock"
            context = multiprocessing.get_context("spawn")
            ready = context.Queue()
            release = context.Queue()
            process = context.Process(
                target=hold_lock,
                args=(str(lock_path), ready, release),
            )
            process.start()
            try:
                self.assertTrue(ready.get(timeout=5))
                with self.assertRaisesRegex(InterprocessLockError, "超时"):
                    with exclusive_file_lock(lock_path, timeout=0.05):
                        pass
            finally:
                release.put(True)
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)


if __name__ == "__main__":
    unittest.main()
