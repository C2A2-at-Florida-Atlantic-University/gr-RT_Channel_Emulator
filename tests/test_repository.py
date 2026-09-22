"""Verify the single-demo layout, documentation links, and GRC generation."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples/channel_estimation_demo.grc"


class RepositoryTest(unittest.TestCase):
    def test_only_one_grc_demo_is_maintained(self):
        self.assertEqual(list((ROOT / "examples").glob("*.grc")), [DEMO])

    def test_demo_uses_two_external_position_sockets(self):
        demo = DEMO.read_text()
        definition = (ROOT / "grc/rt_channel_emulation_rt_channel_emulation.block.yml").read_text()
        self.assertEqual(demo.count("  id: network_socket_pdu\n"), 2)
        self.assertEqual(demo.count("    type: UDP_SERVER\n"), 2)
        for node, port in (("tx", 52001), ("rx", 52002)):
            self.assertIn(f"[{node}_position_socket, pdus, rt_channel, {node}_position]", demo)
            self.assertIn(f"    port: '{port}'", demo)
            self.assertIn(f"- id: {node}_position\n  label: {node}_position\n  domain: message", definition)
        for removed in ("mobility_enabled", "mobility_host", "mobility_port"):
            self.assertNotIn(removed, demo)
            self.assertNotIn(removed, definition)

    def test_readme_local_links_exist(self):
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", (ROOT / "README.md").read_text()):
            if not target.startswith("https://"):
                with self.subTest(target=target):
                    self.assertTrue((ROOT / target).exists())

    def test_package_helpers_remain_importable_without_gnu_radio(self):
        # Isolate the unavailable-GNU-Radio case from other streaming tests.
        code = """
import sys
sys.modules['gnuradio'] = None
from rt_channel_emulation import GNURADIO_AVAILABLE, RTChannelEmulation, resolve_scene_path
assert not GNURADIO_AVAILABLE
assert resolve_scene_path('FAU_scene')
try:
    RTChannelEmulation()
except ImportError:
    pass
else:
    raise AssertionError('constructing the block must require GNU Radio')
"""
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=10,
            env={**os.environ, "PYTHONPATH": str(ROOT / "python")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("grcc"), "GRC compiler is not installed")
    def test_demo_generates_valid_python(self):
        # Generated Python belongs in a temporary build directory, not source.
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [shutil.which("grcc"), "-o", directory, str(DEMO)],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "GRC_BLOCKS_PATH": str(ROOT / "grc"),
                     "PYTHONPATH": str(ROOT / "python")},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            generated = Path(directory) / "channel_estimation_demo.py"
            source = generated.read_text()
            compile(source, str(generated), "exec")
            # Each L-tap vector becomes one L-point stem frame. A fixed zero
            # lower bound prevents autoscaling from cropping positive stems.
            self.assertIn("self.tap_plot = qtgui.time_sink_f(", source)
            self.assertIn("self.tap_plot.enable_stem_plot(True)", source)
            self.assertIn("self.tap_plot.enable_autoscale(False)", source)
            self.assertRegex(source, r"self\.tap_plot\.set_y_axis\(0(?:\.0)?,")
            self.assertRegex(source, r"qtgui.time_sink_f\(\s*num_taps,.*\n\s*samp_rate,")
            for port, prefix in enumerate(("ls", "rt")):
                self.assertIn(
                    f"self.{prefix}_tap_stream = blocks.vector_to_stream(gr.sizeof_float*1, num_taps)",
                    source,
                )
                self.assertIn(
                    f"self.connect((self.{prefix}_magnitude, 0), (self.{prefix}_tap_stream, 0))",
                    source,
                )
                self.assertIn(
                    f"self.connect((self.{prefix}_tap_stream, 0), (self.tap_plot, {port}))",
                    source,
                )
            self.assertNotIn("self.tap_plot.set_output_multiple(2)", source)
