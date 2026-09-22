from __future__ import annotations

from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rt_channel_emulation.backend import SimulationConfig, resolve_scene_path  # noqa: E402
from rt_channel_emulation.gui_launcher import (  # noqa: E402
    ITU_MATERIAL_COLORS,
    NODE_COLORS,
    PATH_COLORS,
    UNKNOWN_MATERIAL_COLOR,
    camera_axes,
    canonical_material_name,
    fit_node_view,
    ground_shadow,
    load_ply_edges,
    load_scene_geometry,
    project_points,
    view_depth,
)
from rt_channel_emulation.visualization import (  # noqa: E402
    QtSceneVisualizer,
    build_gui_command,
)


def _config(visualize: bool = True) -> SimulationConfig:
    return SimulationConfig(
        scene_path="FAU_scene",
        tx_position=(1, 2, 3),
        rx_position=(4, 5, 6),
        sample_rate=1e6,
        visualize=visualize,
    )


def _segments() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return np.zeros((1, 3)), np.ones((1, 3)), np.full((1, 3), 0.5)


class VisualizationLifecycleTest(unittest.TestCase):
    def test_command_uses_current_python_scene_and_state(self):
        command = build_gui_command("/tmp/scene.xml", "/tmp/state.npz")

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[command.index("--scene") + 1], "/tmp/scene.xml")
        self.assertEqual(command[command.index("--state") + 1], "/tmp/state.npz")
        self.assertEqual(command[command.index("--parent-pid") + 1], str(os.getpid()))

    @unittest.skipUnless(importlib.util.find_spec("PyQt5"), "PyQt5 is not installed")
    def test_viewer_exits_if_parent_is_gone(self):
        """Exercise Qt drawing and shutdown, including no meshes or no paths."""
        for alias in ("FAU_scene", "LOS_empty", "NLOS_box"):
            with self.subTest(scene=alias):
                visualizer = QtSceneVisualizer()
                self.addCleanup(visualizer.close)
                scene_path = resolve_scene_path(alias)
                segments = (_segments() if alias != "NLOS_box"
                            else tuple(np.empty((0, 3)) for _ in range(3)))
                config = replace(_config(), scene_path=alias,
                                 tx_position=(-5, 0, 1), rx_position=(5, 0, 1))
                # Make a valid state file without starting a child yet.
                with patch("rt_channel_emulation.visualization.subprocess.Popen"):
                    visualizer.update(config, scene_path, segments)
                command = build_gui_command(scene_path, str(visualizer._state_path))
                # No user process can have parent PID -1: model an orphaned viewer.
                command[-1] = "-1"
                result = subprocess.run(command, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    @patch("rt_channel_emulation.visualization.subprocess.Popen")
    def test_mobility_updates_state_without_restarting_viewer(self, popen):
        process = MagicMock()
        process.poll.return_value = None
        popen.return_value = process
        visualizer = QtSceneVisualizer()

        visualizer.update(_config(), "/tmp/scene.xml", _segments())
        updated = replace(_config(), tx_position=(7, 8, 9))
        visualizer.update(updated, "/tmp/scene.xml", _segments())

        state_path = visualizer._state_path
        self.assertIsNotNone(state_path)
        with np.load(state_path) as state:
            np.testing.assert_array_equal(state["tx_position"], (7, 8, 9))
        self.assertEqual(popen.call_count, 1)

        visualizer.update(replace(updated, visualize=False), None, None)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)
        self.assertFalse(state_path.exists())

    @patch("rt_channel_emulation.visualization.subprocess.Popen")
    def test_scene_change_restarts_viewer(self, popen):
        first_process = MagicMock()
        first_process.poll.return_value = None
        second_process = MagicMock()
        second_process.poll.return_value = None
        popen.side_effect = (first_process, second_process)
        visualizer = QtSceneVisualizer()

        visualizer.update(_config(), "/tmp/first.xml", _segments())
        visualizer.update(_config(), "/tmp/second.xml", _segments())

        self.assertEqual(popen.call_count, 2)
        first_process.terminate.assert_called_once_with()
        visualizer.close()


class SceneGeometryTest(unittest.TestCase):
    def test_node_and_enabled_path_colors_are_labeled(self):
        self.assertEqual(set(NODE_COLORS), {"TX node", "RX node"})
        self.assertEqual(
            set(PATH_COLORS),
            {
                "LoS / initial segment",
                "Specular reflection",
                "Refraction / transmission",
            },
        )
        self.assertTrue(
            all(
                0.0 <= component <= 1.0
                for color in (*NODE_COLORS.values(), *PATH_COLORS.values())
                for component in color
            )
        )

    def test_all_sionna_itu_material_types_have_colors(self):
        expected = {
            "itu_vacuum",
            "itu_concrete",
            "itu_brick",
            "itu_plasterboard",
            "itu_wood",
            "itu_glass",
            "itu_ceiling_board",
            "itu_chipboard",
            "itu_plywood",
            "itu_marble",
            "itu_floorboard",
            "itu_metal",
            "itu_very_dry_ground",
            "itu_medium_dry_ground",
            "itu_wet_ground",
        }

        self.assertEqual(set(ITU_MATERIAL_COLORS), expected)
        self.assertTrue(all(len(color) == 3 for color in ITU_MATERIAL_COLORS.values()))

    def test_material_reference_names_are_normalized(self):
        self.assertEqual(canonical_material_name("mat-itu_brick"), "itu_brick")
        self.assertEqual(canonical_material_name("itu-brick"), "itu_brick")
        self.assertEqual(canonical_material_name(None), "unassigned")

    def test_ascii_ply_is_converted_to_polygon_edges(self):
        contents = """ply
format ascii 1.0
element vertex 3
property float x
property float y
property float z
element face 1
property list uchar int vertex_indices
end_header
0 0 0
1 0 0
0 1 0
3 0 1 2
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "triangle.ply"
            path.write_text(contents, encoding="ascii")
            edges = load_ply_edges(path)

        self.assertEqual(edges.shape, (3, 2, 3))
        np.testing.assert_array_equal(edges[0], ((0, 0, 0), (1, 0, 0)))

    def test_bundled_fau_scene_binary_meshes_are_supported(self):
        scene = load_scene_geometry(resolve_scene_path("FAU_scene"))

        self.assertEqual(scene.edges.shape, (1662, 2, 3))
        self.assertEqual(scene.colors.shape, (1662, 3))
        self.assertEqual(len(scene.polygons), len(scene.polygon_colors))
        self.assertTrue(all(polygon.shape[1] == 3 for polygon in scene.polygons))
        self.assertTrue(np.all(np.isfinite(scene.edges)))
        self.assertEqual(
            {name for name, _color in scene.materials},
            {
                "itu_concrete",
                "itu_glass",
                "itu_metal",
                "itu_medium_dry_ground",
                "itu_wet_ground",
            },
        )
        self.assertNotIn(UNKNOWN_MATERIAL_COLOR, {color for _name, color in scene.materials})
        self.assertEqual(
            {tuple(color) for color in np.unique(scene.colors, axis=0)},
            {ITU_MATERIAL_COLORS[name] for name, _color in scene.materials},
        )

    def test_bundled_powder_scene_geometry_and_materials_are_supported(self):
        scene = load_scene_geometry(resolve_scene_path("POWDER"))

        self.assertEqual(len(scene.polygons), 2167)
        self.assertEqual(scene.edges.shape, (6501, 2, 3))
        self.assertEqual(
            {name for name, _color in scene.materials},
            {"itu_brick", "itu_concrete", "itu_glass", "itu_metal"},
        )
        self.assertTrue(np.all(np.isfinite(scene.edges)))
        self.assertEqual(
            {tuple(color) for color in np.unique(scene.polygon_colors, axis=0)},
            {ITU_MATERIAL_COLORS[name] for name, _color in scene.materials},
        )

    def test_projection_preserves_point_count(self):
        projected = project_points(np.array(((0, 0, 0), (1, 2, 3))))

        self.assertEqual(projected.shape, (2, 2))
        np.testing.assert_array_equal(projected[0], (0, 0))

    def test_view_depth_increases_toward_isometric_camera(self):
        depths = view_depth(np.array(((0, 0, 0), (1, 1, 1))))

        self.assertGreater(depths[1], depths[0])


class CameraAndShadowTest(unittest.TestCase):
    def test_rotating_camera_preserves_lengths_and_fits_nodes(self):
        nodes = np.array(((-100, 20, 1), (50, -70, 30)))
        for elevation in (5, 20, 85):
            for azimuth in (-180, -90, 0, 45, 180):
                with self.subTest(elevation=elevation, azimuth=azimuth):
                    axes = camera_axes(elevation, azimuth)
                    projected = project_points(nodes, axes)
                    depth = view_depth(nodes, axes)
                    np.testing.assert_allclose(
                        np.sum(projected ** 2, axis=1) + depth ** 2,
                        np.sum(nodes ** 2, axis=1),
                    )
                    center, scale = fit_node_view(*nodes, (520, 495), axes)
                    self.assertTrue(np.all(np.abs((projected - center) * scale) < (260, 247.5)))

    def test_cardinal_side_view_projects_horizontal_and_height(self):
        axes = camera_axes(0, 0)  # View from +X: Y is right, Z is up.
        np.testing.assert_allclose(project_points((3, 4, 5), axes), (4, 5))
        self.assertEqual(view_depth((3, 4, 5), axes), 3)

    def test_camera_keeps_both_nodes_inside_viewport_with_padding(self):
        for tx, rx in (
            ((28, -25, 1), (28, 25, 1)),
            ((-500, -200, 1), (400, 300, 100)),
            ((0, 0, 0), (0, 0, 100)),
            ((3, 4, 5), (3, 4, 5)),
        ):
            for size in ((520, 495), (1200, 400)):
                with self.subTest(tx=tx, rx=rx, size=size):
                    center, scale = fit_node_view(tx, rx, size)
                    pixels = (project_points((tx, rx)) - center) * scale
                    self.assertTrue(np.isfinite(scale) and scale > 0)
                    self.assertTrue(np.all(np.abs(pixels) <= np.asarray(size) / 3 + 1e-8))

    def test_camera_follows_translation_without_changing_zoom(self):
        nodes = np.array(((0, 0, 2), (10, 0, 2)))
        movement = np.array((100, -300, 15))
        center, scale = fit_node_view(*nodes, (520, 495))
        moved_center, moved_scale = fit_node_view(*(nodes + movement), (520, 495))
        np.testing.assert_allclose(moved_center - center, project_points(movement))
        self.assertAlmostEqual(scale, moved_scale)

    def test_camera_zooms_out_when_nodes_separate(self):
        _, near_scale = fit_node_view((0, 0, 1), (10, 0, 1), (520, 495))
        _, far_scale = fit_node_view((0, 0, 1), (100, 0, 1), (520, 495))
        self.assertGreater(near_scale, far_scale)

    def test_shadow_projects_onto_ground_away_from_light(self):
        points = np.array(((0, 0, 12), (5, 6, 2)), dtype=float)
        before = points.copy()
        shadow = ground_shadow(points, ground_z=2)
        np.testing.assert_allclose(shadow, ((6, 8, 2), (5, 6, 2)))
        np.testing.assert_array_equal(points, before)


if __name__ == "__main__":
    unittest.main()
