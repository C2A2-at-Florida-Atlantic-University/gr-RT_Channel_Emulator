from __future__ import annotations

from dataclasses import replace
import logging
import threading
from typing import Any, Callable, Mapping

from .backend import ChannelSnapshot, SimulationConfig

LOGGER = logging.getLogger(__name__)

SIMULATION_KEYS = {
    "scene_path",
    "tx_position",
    "rx_position",
    "sample_rate",
    "carrier_frequency_hz",
    "max_depth",
    "l_min",
    "l_max",
    "tx_pattern",
    "tx_polarization",
    "rx_pattern",
    "rx_polarization",
    "seed",
}
GNU_RADIO_ONLY_KEYS = {
    "noise_voltage",
    "frequency_offset",
    "epsilon",
}
VISUALIZATION_KEYS = {"visualize"}
CONFIG_KEYS = SIMULATION_KEYS | GNU_RADIO_ONLY_KEYS | VISUALIZATION_KEYS


class ChannelModelController:
    """Recompute Sionna taps only when a ray-tracing input changes."""

    def __init__(
        self,
        backend: Any,
        channel_model: Any,
        config: SimulationConfig,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.backend = backend
        self.channel_model = channel_model
        self.config = config
        self.snapshot: ChannelSnapshot | None = None
        # A lightweight observer publishes metadata, never runs ray tracing.
        self.on_update = on_update
        self.revision = 0
        # GRC callbacks and message handlers may update the same controller
        # from different threads. One lock keeps each configuration atomic.
        self._lock = threading.RLock()

    def initialize(self) -> ChannelSnapshot:
        with self._lock:
            self.snapshot = self.backend.compute_snapshot(self.config)
            self._apply_snapshot(self.snapshot)
            self._notify_update()
            return self.snapshot

    def channel_estimate(self) -> dict[str, Any]:
        """Return the applied model reference, not an estimate from RX samples.

        ``taps[j]`` is the Sionna coefficient for lag ``l_min + j``. The
        causal GNU Radio FIR uses this same coefficient at sample delay j.
        ``effective_taps`` remains a compatibility alias for ``taps``. Neither
        vector includes noise or external amplitude scaling. The FIR reference
        describes the signal path when frequency_offset=0 and epsilon=1.

        A revision identifies an applied configuration, not a stream sample:
        asynchronous message delivery does not mark an exact tap-change boundary.
        """
        with self._lock:
            if self.snapshot is None:
                raise RuntimeError("No channel estimate is available before initialize")
            return {
                "schema_version": 2,
                "revision": self.revision,
                "scene_path": self.snapshot.resolved_scene_path,
                "tx_position": list(self.config.tx_position),
                "rx_position": list(self.config.rx_position),
                "sample_rate_hz": self.config.sample_rate,
                "carrier_frequency_hz": self.config.carrier_frequency_hz,
                "l_min": self.config.l_min,
                "taps": list(self.snapshot.taps),
                "effective_taps": list(self.snapshot.taps),
                "has_paths": self.snapshot.has_paths,
                "noise_voltage": self.config.noise_voltage,
                "frequency_offset": self.config.frequency_offset,
                "epsilon": self.config.epsilon,
            }

    def _notify_update(self) -> None:
        """Publish only after applying taps and runtime parameters, under the lock."""
        self.revision += 1
        if self.on_update is not None:
            self.on_update(self.channel_estimate())

    def update(self, updates: Mapping[str, Any]) -> bool:
        """Apply one atomic configuration update from GRC or PMT messages."""
        with self._lock:
            return self._update_unlocked(updates)

    def _update_unlocked(self, updates: Mapping[str, Any]) -> bool:
        known_updates = {}
        for key, value in updates.items():
            if key not in CONFIG_KEYS:
                LOGGER.warning("Ignoring unknown config key: %s", key)
                continue
            known_updates[key] = value

        if not known_updates:
            return False

        config = replace(self.config, **known_updates)
        visualization_enabled = "visualize" in known_updates and config.visualize
        needs_recompute = (
            bool(set(known_updates) & SIMULATION_KEYS)
            or visualization_enabled
            or self.snapshot is None
        )
        if needs_recompute:
            # A failed trace must not pair the previous taps with new positions
            # in the public channel estimate. Commit only after tracing succeeds.
            snapshot = self.backend.compute_snapshot(config)
            self.config = config
            self.snapshot = snapshot
            self._apply_snapshot(self.snapshot)
            self._notify_update()
            return True

        self.config = config
        if set(known_updates) & VISUALIZATION_KEYS:
            update_visualization = getattr(self.backend, "update_visualization", None)
            if update_visualization is not None:
                update_visualization(self.config)

        self._apply_runtime_parameters()
        self._notify_update()
        return False

    def _apply_snapshot(self, snapshot: ChannelSnapshot) -> None:
        self.channel_model.set_taps(list(snapshot.taps))
        self._apply_runtime_parameters()

    def _apply_runtime_parameters(self) -> None:
        if self.snapshot is None:
            raise RuntimeError("No snapshot is available to apply")

        # Same linear input as channels.channel_model: E[|w|^2] = noise_voltage^2.
        self.channel_model.set_noise_voltage(self.config.noise_voltage)

        if hasattr(self.channel_model, "set_frequency_offset"):
            self.channel_model.set_frequency_offset(self.config.frequency_offset)

        if hasattr(self.channel_model, "set_timing_offset"):
            self.channel_model.set_timing_offset(self.config.epsilon)
        elif hasattr(self.channel_model, "set_epsilon"):
            self.channel_model.set_epsilon(self.config.epsilon)
