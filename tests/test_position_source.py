from __future__ import annotations

from pathlib import Path
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.position_source import build_message, main, parse_args, position_at  # noqa: E402


class PositionSourceTest(unittest.TestCase):
    def test_position_at_uses_constant_velocity(self):
        self.assertEqual(position_at((1, 2, 3), (2, -1, 0.5), 2.0), (5.0, 0.0, 4.0))

    def test_build_message_updates_both_nodes_at_same_time(self):
        message = build_message(
            tx_start=(0, 0, 1),
            rx_start=(10, 0, 1),
            tx_velocity=(1, 0, 0),
            rx_velocity=(-1, 0, 0),
            elapsed_s=2.0,
        )

        self.assertEqual(message["tx_position"], (2.0, 0.0, 1.0))
        self.assertEqual(message["rx_position"], (8.0, 0.0, 1.0))

    def test_sender_routes_each_json_array_to_its_socket(self):
        argv = ["position_source.py", "--tx-start", "0", "0", "1",
                "--rx-start", "10", "0", "1", "--tx-velocity", "1", "0", "0",
                "--rx-velocity", "-1", "0", "0", "--rate-hz", "0.5", "--updates", "2",
                "--tx-port", "53001", "--rx-port", "53002"]
        with patch.object(sys, "argv", argv), patch("scripts.position_source.socket.socket") as factory:
            with patch("scripts.position_source.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
                main()
        sent = factory.return_value.__enter__.return_value.sendto.call_args_list
        self.assertEqual([(json.loads(call.args[0]), call.args[1]) for call in sent], [
            ([0, 0, 1], ("127.0.0.1", 53001)), ([10, 0, 1], ("127.0.0.1", 53002)),
            ([2, 0, 1], ("127.0.0.1", 53001)), ([8, 0, 1], ("127.0.0.1", 53002)),
        ])

    def test_rejects_invalid_ports_rate_and_coordinates(self):
        base = ["position_source.py", "--tx-start", "0", "0", "1", "--rx-start", "10", "0", "1"]
        for options in (["--tx-port", "0"], ["--rx-port", "65536"],
                        ["--rx-port", "52001"], ["--rate-hz", "0"], ["--rate-hz", "nan"],
                        ["--tx-start", "nan", "0", "1"], ["--updates", "-1"]):
            with self.subTest(options=options), patch.object(sys, "argv", base + options):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parse_args()


if __name__ == "__main__":
    unittest.main()
