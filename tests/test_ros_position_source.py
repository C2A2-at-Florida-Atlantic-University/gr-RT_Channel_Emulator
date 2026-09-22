"""Test frame validation and coalescing without requiring ROS to be installed."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
for directory in (REPO_ROOT, REPO_ROOT / "python"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from scripts.ros_position_source import fresh_positions, pose_position


class RosPositionTest(unittest.TestCase):
    def test_extracts_position_in_scene_frame(self):
        message = SimpleNamespace(
            header=SimpleNamespace(frame_id="scene"),
            pose=SimpleNamespace(position=SimpleNamespace(x=28, y=-25, z=1)),
        )
        self.assertEqual(pose_position(message, "scene"), [28.0, -25.0, 1.0])
        with self.assertRaisesRegex(ValueError, "frame_id"):
            pose_position(message, "other_map")
        message.pose.position.z = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            pose_position(message, "scene")

    def test_waits_for_both_nodes_and_rejects_stale_data(self):
        positions = {"tx_position": [1, 2, 3]}
        arrivals = {"tx_position": 10}
        self.assertIsNone(fresh_positions(positions, arrivals, 10, 2))
        positions["rx_position"] = [4, 5, 6]
        arrivals["rx_position"] = 11
        self.assertEqual(fresh_positions(positions, arrivals, 11, 2), positions)
        self.assertIsNone(fresh_positions(positions, arrivals, 13, 2))

    def test_newest_pair_contains_one_json_array_per_socket(self):
        import json

        positions = {"tx_position": [1, 2, 3], "rx_position": [4, 5, 6]}
        arrivals = {"tx_position": 10, "rx_position": 10}
        positions["tx_position"] = [7, 8, 9]
        message = fresh_positions(positions, arrivals, 10, 2)
        self.assertEqual(json.loads(json.dumps(message["tx_position"])), [7, 8, 9])
        self.assertEqual(json.loads(json.dumps(message["rx_position"])), [4, 5, 6])
