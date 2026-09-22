from __future__ import annotations

from pathlib import Path
import os
import sys
import unittest

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rt_channel_emulation.backend import (  # noqa: E402
    SimulationConfig,
    extract_single_link_taps,
    resolve_scene_path,
)


class BackendHelpersTest(unittest.TestCase):
    def test_simulation_config_parses_tuple_strings(self):
        config = SimulationConfig(
            scene_path="FAU_scene",
            tx_position="(1, 2, 3)",
            rx_position=[4, 5, 6],
            sample_rate=1e6,
            noise_voltage='0.01',
            l_min=-2,
            l_max=8,
        )

        self.assertEqual(config.tx_position, (1.0, 2.0, 3.0))
        self.assertEqual(config.rx_position, (4.0, 5.0, 6.0))
        self.assertEqual(config.sample_rate, 1e6)
        self.assertEqual(config.noise_voltage, 0.01)

    def test_simulation_config_parses_visualization_boolean(self):
        config = SimulationConfig(
            scene_path="FAU_scene",
            tx_position=(0, 0, 1),
            rx_position=(1, 0, 1),
            sample_rate=1e6,
            visualize="true",
        )

        self.assertTrue(config.visualize)

        with self.assertRaisesRegex(ValueError, "visualize must be true or false"):
            SimulationConfig(
                scene_path="FAU_scene",
                tx_position=(0, 0, 1),
                rx_position=(1, 0, 1),
                sample_rate=1e6,
                visualize="sometimes",
            )

    def test_invalid_noise_voltage_raises(self):
        for value in (-0.1, float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=value), self.assertRaisesRegex(
                    ValueError, "noise_voltage must be finite and non-negative"):
                SimulationConfig("FAU_scene", (0, 0, 1), (1, 0, 1), 1e6,
                                 noise_voltage=value)
        self.assertEqual(SimulationConfig("FAU_scene", (0, 0, 1), (1, 0, 1), 1e6).noise_voltage, 0)

    def test_non_finite_position_raises(self):
        with self.assertRaisesRegex(ValueError, "tx_position must contain finite values"):
            SimulationConfig(
                scene_path="FAU_scene",
                tx_position=(float("nan"), 0, 1),
                rx_position=(1, 0, 1),
                sample_rate=1e6,
            )

    def test_removed_gain_and_db_noise_parameters_are_not_silently_accepted(self):
        for name in ("tx_gain_db", "rx_gain_db", "noise_voltage_db"):
            with self.subTest(name=name), self.assertRaises(TypeError):
                SimulationConfig("FAU_scene", (0, 0, 1), (1, 0, 1), 1e6, **{name: 0})

    def test_resolve_scene_path_supports_bundled_aliases(self):
        expected_names = {
            "FAU_scene": "FAU_scene.xml",
            "POWDER": "POWDER_dense.xml",
            "AERPAW_scene": "AERPAW.xml",
            "LOS_empty": "LOS_empty.xml",
            "NLOS_box": "NLOS_box.xml",
        }

        for alias, expected_name in expected_names.items():
            with self.subTest(alias=alias):
                resolved = Path(resolve_scene_path(alias))
                self.assertTrue(resolved.exists())
                self.assertEqual(resolved.name, expected_name)

    def test_resolve_scene_path_accepts_existing_and_packaged_paths(self):
        expected = resolve_scene_path("FAU_scene")
        for value in (expected, os.path.relpath(expected), "FAU_scene/FAU_scene.xml"):
            with self.subTest(value=value):
                self.assertEqual(resolve_scene_path(value), expected)

    def test_extract_single_link_taps_flattens_singleton_dimensions(self):
        taps = np.array([[[[[[1 + 0j, 0.5 - 0.25j, 0j]]]]]])
        flat_taps = extract_single_link_taps(taps)
        np.testing.assert_allclose(flat_taps, np.array([1 + 0j, 0.5 - 0.25j, 0j]))

    def test_extract_single_link_taps_rejects_multiple_streams(self):
        taps = np.array([[[[[[1 + 0j, 0.5 + 0j, 0j]]]], [[[[0.25 + 0j, -0.25 + 0j, 0j]]]]]])
        with self.assertRaisesRegex(ValueError, "one TX, one RX"):
            extract_single_link_taps(taps)

    def test_invalid_tap_shape_raises(self):
        with self.assertRaisesRegex(ValueError, "at least 1-dimensional"):
            extract_single_link_taps(np.array(1.0))
