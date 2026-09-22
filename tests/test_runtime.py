from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rt_channel_emulation.backend import ChannelSnapshot, SimulationConfig  # noqa: E402
from rt_channel_emulation.runtime import ChannelModelController


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.visualization_calls = []

    def compute_snapshot(self, config: SimulationConfig) -> ChannelSnapshot:
        self.calls.append(config)
        base_tap = complex(len(self.calls), 0.0)
        return ChannelSnapshot(
            resolved_scene_path=f"/tmp/{config.scene_path}",
            taps=(base_tap, 0.0j),
            has_paths=True,
        )

    def update_visualization(self, config: SimulationConfig) -> None:
        self.visualization_calls.append(config)


class FakeChannelModel:
    def __init__(self):
        self.taps = None
        self.noise_voltage = None
        self.frequency_offset = None
        self.timing_offset = None

    def set_taps(self, taps):
        self.taps = list(taps)

    def set_noise_voltage(self, value):
        self.noise_voltage = value

    def set_frequency_offset(self, value):
        self.frequency_offset = value

    def set_timing_offset(self, value):
        self.timing_offset = value


class ChannelModelControllerTest(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.channel_model = FakeChannelModel()
        self.config = SimulationConfig(
            scene_path="FAU_scene",
            tx_position=(0, 0, 1),
            rx_position=(1, 0, 1),
            sample_rate=1e6,
            noise_voltage=0.1,
        )
        self.controller = ChannelModelController(
            self.backend,
            self.channel_model,
            self.config,
        )

    def test_initialize_computes_and_applies_snapshot(self):
        snapshot = self.controller.initialize()
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.channel_model.taps, [1 + 0j, 0j])
        self.assertTrue(snapshot.has_paths)
        self.assertAlmostEqual(self.channel_model.noise_voltage, 0.1)

    def test_estimate_contains_propagation_taps_and_linear_noise(self):
        events = []
        self.controller.on_update = events.append
        self.controller.initialize()
        self.controller.update({"noise_voltage": 0.2})
        reference = self.controller.channel_estimate()
        self.assertEqual(reference["taps"], [1 + 0j, 0j])
        self.assertEqual(reference["effective_taps"], reference["taps"])
        self.assertEqual(reference["schema_version"], 2)
        self.assertEqual(reference["noise_voltage"], 0.2)
        self.assertNotIn("tx_gain_db", reference)
        self.assertNotIn("rx_gain_db", reference)
        self.assertEqual(reference["revision"], 2)
        self.assertEqual(reference["sample_rate_hz"], 1e6)
        self.assertEqual(reference["l_min"], -6)
        self.assertEqual([event["revision"] for event in events], [1, 2])
        self.assertEqual(len(self.backend.calls), 1)
        # Returned lists are copies; consumers cannot mutate the model.
        reference["taps"][0] = 0j
        self.assertEqual(self.controller.snapshot.taps[0], 1 + 0j)

    def test_failed_trace_preserves_reference_and_revision(self):
        self.controller.initialize()
        previous = self.controller.channel_estimate()
        observer = Mock()
        self.controller.on_update = observer
        self.backend.compute_snapshot = Mock(side_effect=RuntimeError("trace failed"))
        with self.assertRaisesRegex(RuntimeError, "trace failed"):
            self.controller.update({"tx_position": (100, 0, 1)})
        self.assertEqual(self.controller.channel_estimate(), previous)
        observer.assert_not_called()

    def test_gr_only_update_does_not_trigger_recompute(self):
        self.controller.initialize()
        recomputed = self.controller.update(
            {"frequency_offset": 0.125, "noise_voltage": 0.25}
        )
        self.assertFalse(recomputed)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.channel_model.frequency_offset, 0.125)
        self.assertEqual(self.channel_model.noise_voltage, 0.25)

    def test_noise_can_be_disabled_without_retracing_or_changing_taps(self):
        self.controller.initialize()
        previous_taps = self.controller.channel_estimate()["taps"]
        self.assertFalse(self.controller.update({"noise_voltage": 0.0}))
        self.assertEqual(self.channel_model.noise_voltage, 0.0)
        self.assertEqual(self.controller.channel_estimate()["taps"], previous_taps)
        self.assertEqual(len(self.backend.calls), 1)

    def test_invalid_noise_update_preserves_previous_configuration(self):
        self.controller.initialize()
        previous = self.controller.channel_estimate()
        with self.assertRaises(ValueError):
            self.controller.update({"noise_voltage": -1})
        self.assertEqual(self.controller.channel_estimate(), previous)

    def test_position_update_triggers_recompute(self):
        self.controller.initialize()
        recomputed = self.controller.update({"tx_position": (10, 0, 1)})
        self.assertTrue(recomputed)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(self.channel_model.taps, [2 + 0j, 0j])

    def test_combined_tx_rx_update_traces_once(self):
        self.controller.initialize()
        recomputed = self.controller.update(
            {"tx_position": (10, 0, 1), "rx_position": (20, 0, 1)}
        )

        self.assertTrue(recomputed)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(self.controller.config.tx_position, (10.0, 0.0, 1.0))
        self.assertEqual(self.controller.config.rx_position, (20.0, 0.0, 1.0))

    def test_enabling_visualization_recomputes_paths(self):
        self.controller.initialize()
        recomputed = self.controller.update({"visualize": True})

        self.assertTrue(recomputed)
        self.assertEqual(len(self.backend.calls), 2)

    def test_disabling_visualization_closes_viewer_without_recompute(self):
        self.controller.initialize()
        self.controller.update({"visualize": True})
        recomputed = self.controller.update({"visualize": False})

        self.assertFalse(recomputed)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(len(self.backend.visualization_calls), 1)
        self.assertFalse(self.backend.visualization_calls[0].visualize)

    def test_unknown_keys_are_ignored(self):
        self.controller.initialize()
        recomputed = self.controller.update({"not_a_key": 1})
        self.assertFalse(recomputed)
        self.assertEqual(len(self.backend.calls), 1)

    def test_malformed_position_update_raises(self):
        self.controller.initialize()
        with self.assertRaisesRegex(ValueError, "3-element coordinate"):
            self.controller.update({"rx_position": (1, 2)})
