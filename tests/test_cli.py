import argparse
import unittest

from companion_kit.cli import _local_port


class CliTests(unittest.TestCase):
    def test_local_port_accepts_auto_and_rejects_out_of_range_values(self) -> None:
        self.assertEqual(_local_port("0"), 0)
        self.assertEqual(_local_port("65535"), 65535)

        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("65536")
        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("-1")
        with self.assertRaises(argparse.ArgumentTypeError):
            _local_port("not-a-port")


if __name__ == "__main__":
    unittest.main()
