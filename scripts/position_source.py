#!/usr/bin/env python3
"""Send linear TX/RX motion to the demo's two UDP Socket PDU inputs.

The discrete constant-velocity model is

    position[index] = start + velocity * index / rate_hz

Positions are metres, velocities are metres/second, and ``rate_hz`` is the
requested number of updates per second per node. Each datagram contains one
UTF-8 JSON [X, Y, Z] array. The first pair uses the exact starting positions.
UDP does not guarantee delivery or ordering; the two updates are independent.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time
from typing import Sequence

Position = tuple[float, float, float]


def position_at(start: Sequence[float], velocity: Sequence[float], elapsed_s: float) -> Position:
    """Return ``start + velocity * elapsed_s`` for one three-dimensional node."""
    if len(start) != 3 or len(velocity) != 3:
        raise ValueError("start and velocity must each contain three values")
    return tuple(float(start[i]) + float(velocity[i]) * elapsed_s for i in range(3))


def build_message(
    tx_start: Sequence[float],
    rx_start: Sequence[float],
    tx_velocity: Sequence[float],
    rx_velocity: Sequence[float],
    elapsed_s: float,
) -> dict[str, Position]:
    """Calculate both nodes at the same model time; delivery is not atomic."""
    return {
        "tx_position": position_at(tx_start, tx_velocity, elapsed_s),
        "rx_position": position_at(rx_start, rx_velocity, elapsed_s),
    }


def parse_args() -> argparse.Namespace:
    """Parse socket, initial-position, velocity, and update-rate inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="demo Socket PDU host")
    parser.add_argument("--tx-port", type=int, default=52001, help="TX UDP destination port")
    parser.add_argument("--rx-port", type=int, default=52002, help="RX UDP destination port")
    parser.add_argument(
        "--tx-start",
        type=float,
        nargs=3,
        required=True,
        metavar=("X", "Y", "Z"),
        help="initial TX position in metres",
    )
    parser.add_argument(
        "--rx-start",
        type=float,
        nargs=3,
        required=True,
        metavar=("X", "Y", "Z"),
        help="initial RX position in metres",
    )
    parser.add_argument(
        "--tx-velocity",
        type=float,
        nargs=3,
        default=(0.0, 0.0, 0.0),
        metavar=("VX", "VY", "VZ"),
        help="TX velocity in metres/second; default: stationary",
    )
    parser.add_argument(
        "--rx-velocity",
        type=float,
        nargs=3,
        default=(0.0, 0.0, 0.0),
        metavar=("VX", "VY", "VZ"),
        help="RX velocity in metres/second; default: stationary",
    )
    parser.add_argument("--rate-hz", type=float, default=1.0, help="updates/second")
    parser.add_argument(
        "--updates",
        type=int,
        default=0,
        help="number of updates; 0 continues until Ctrl-C",
    )
    args = parser.parse_args()
    if any(not 1 <= port <= 65535 for port in (args.tx_port, args.rx_port)):
        parser.error("--tx-port and --rx-port must be between 1 and 65535")
    if args.tx_port == args.rx_port:
        parser.error("TX and RX must use different ports")
    if not math.isfinite(args.rate_hz) or args.rate_hz <= 0.0:
        parser.error("--rate-hz must be finite and positive")
    if not all(math.isfinite(value) for values in
               (args.tx_start, args.rx_start, args.tx_velocity, args.rx_velocity)
               for value in values):
        parser.error("positions and velocities must contain finite values")
    if args.updates < 0:
        parser.error("--updates must be non-negative")
    return args


def main() -> None:
    """Send one position array to each destination per model time step."""
    args = parse_args()
    interval_s = 1.0 / args.rate_hz

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            index = 0
            while args.updates == 0 or index < args.updates:
                elapsed_s = index * interval_s
                message = build_message(
                    args.tx_start,
                    args.rx_start,
                    args.tx_velocity,
                    args.rx_velocity,
                    elapsed_s,
                )
                for key, port in (("tx_position", args.tx_port), ("rx_position", args.rx_port)):
                    payload = json.dumps(message[key], allow_nan=False).encode("utf-8")
                    connection.sendto(payload, (args.host, port))
                print(json.dumps(message, allow_nan=False), flush=True)

                index += 1
                if args.updates and index >= args.updates:
                    break
                time.sleep(interval_s)
    except KeyboardInterrupt:
        print("Mobility source stopped.")


if __name__ == "__main__":
    main()
