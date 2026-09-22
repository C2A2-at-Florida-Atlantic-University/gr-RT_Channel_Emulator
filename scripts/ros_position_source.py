#!/usr/bin/env python3
"""ROS 2 PoseStamped topics -> the demo's TX/RX UDP Socket PDU inputs.

Run with a ROS-enabled Python, not necessarily the GNU Radio virtualenv.
Both topics must already express antenna positions in the scene's XYZ frame
in metres. This bridge does not convert GPS, apply tf transforms, or use pose
orientation. See README.md for using this source with the demo.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time


def pose_position(message, frame_id: str) -> list[float]:
    """Extract finite XYZ metres, rejecting a missing or mismatched ROS frame.

    PoseStamped defines position, orientation, timestamp, and frame ID:
    https://github.com/ros2/common_interfaces/blob/rolling/geometry_msgs/msg/PoseStamped.msg
    Only position is consumed. The publisher must transform it to frame_id.
    """
    if not frame_id or message.header.frame_id != frame_id:
        raise ValueError(f"position must use frame_id={frame_id!r}")
    point = message.pose.position
    position = [float(point.x), float(point.y), float(point.z)]
    if not all(math.isfinite(value) for value in position):
        raise ValueError("position coordinates must be finite")
    return position


def fresh_positions(positions: dict, received_at: dict, now: float, max_age: float):
    """Return a complete TX/RX message only while both arrivals are fresh.

    Times are local time.monotonic() seconds. This is an arrival-age guard,
    not synchronization of ROS header timestamps or clocks on remote nodes.
    New callbacks replace old bridge values. GNU Radio can still queue sent
    updates if ray tracing is slower than the configured send rate.
    """
    keys = ("tx_position", "rx_position")
    if any(key not in positions or key not in received_at for key in keys):
        return None
    if any(not 0 <= now - received_at[key] <= max_age for key in keys):
        return None
    return {key: list(positions[key]) for key in keys}


def main() -> None:
    """Subscribe to two topics and send each fresh pair as two JSON datagrams."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tx-topic", default="/tx/pose")
    parser.add_argument("--rx-topic", default="/rx/pose")
    parser.add_argument("--frame-id", required=True, help="frame whose axes/origin match the loaded scene")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--tx-port", type=int, default=52001)
    parser.add_argument("--rx-port", type=int, default=52002)
    parser.add_argument("--rate-hz", type=float, default=1.0)
    parser.add_argument("--max-age-s", type=float, default=2.0)
    args = parser.parse_args()
    if any(not math.isfinite(value) or value <= 0 for value in (args.rate_hz, args.max_age_s)):
        parser.error("rate-hz and max-age-s must be finite and positive")
    if any(not 1 <= port <= 65535 for port in (args.tx_port, args.rx_port)) or not args.frame_id:
        parser.error("ports must be 1..65535 and frame-id must not be empty")
    if args.tx_port == args.rx_port:
        parser.error("TX and RX must use different ports")
    if args.tx_topic == args.rx_topic:
        parser.error("TX and RX must use different topics")

    # ROS stays optional: importing/test-running the helpers requires stdlib only.
    import rclpy
    from geometry_msgs.msg import PoseStamped
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy

    class PositionBridge(Node):
        """Single-threaded ROS callbacks maintain the newest position per node."""

        def __init__(self):
            super().__init__("rt_channel_position_bridge")
            self.positions = {}
            self.received_at = {}
            self.connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # BEST_EFFORT accepts common sensor publishers; depth one retains
            # only the newest unread pose. Each timer tick sends current values.
            qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
            self.create_subscription(
                PoseStamped, args.tx_topic,
                lambda message: self.receive("tx_position", message), qos,
            )
            self.create_subscription(
                PoseStamped, args.rx_topic,
                lambda message: self.receive("rx_position", message), qos,
            )
            self.create_timer(1.0 / args.rate_hz, self.send_positions)

        def receive(self, key, message):
            try:
                self.positions[key] = pose_position(message, args.frame_id)
                self.received_at[key] = time.monotonic()
            except ValueError as error:
                # Do not retain a previously valid pose after a frame error.
                self.positions.pop(key, None)
                self.get_logger().warning(f"Ignoring {key}: {error}")

        def send_positions(self):
            message = fresh_positions(
                self.positions, self.received_at, time.monotonic(), args.max_age_s
            )
            if message is None:
                return
            try:
                # Repeat fresh values so a lost UDP packet is not the only
                # notification of a stationary pose. Delivery is not atomic.
                for key, port in (("tx_position", args.tx_port), ("rx_position", args.rx_port)):
                    payload = json.dumps(message[key], allow_nan=False).encode("utf-8")
                    self.connection.sendto(payload, (args.host, port))
            except OSError as error:
                self.get_logger().warning(f"Position send failed; will retry: {error}")

        def close_socket(self):
            self.connection.close()

    rclpy.init(args=[])
    node = PositionBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close_socket()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
