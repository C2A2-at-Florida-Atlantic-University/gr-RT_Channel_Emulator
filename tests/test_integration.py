from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rt_channel_emulation import GNURADIO_AVAILABLE, RTChannelEmulation, SionnaRTChannelBackend  # noqa: E402
from rt_channel_emulation.backend import ChannelSnapshot, SimulationConfig  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.updated = threading.Event()

    def compute_snapshot(self, config: SimulationConfig) -> ChannelSnapshot:
        self.calls.append(config)
        if len(self.calls) > 1:
            self.updated.set()
        return ChannelSnapshot(
            resolved_scene_path=config.scene_path,
            taps=(1.0 + 0.0j,),
            has_paths=True,
        )


@unittest.skipUnless(GNURADIO_AVAILABLE, "GNU Radio is not installed")
class GNUradioIntegrationTest(unittest.TestCase):
    @patch("rt_channel_emulation.visualization.subprocess.Popen")
    def test_top_block_stop_closes_viewer_without_explicit_channel_stop(self, popen):
        """The scheduler's stop callback must release the owned viewer."""
        from gnuradio import blocks, gr
        import numpy as np
        from rt_channel_emulation.visualization import QtSceneVisualizer

        process = MagicMock()
        process.poll.return_value = None
        popen.return_value = process
        visualizer = QtSceneVisualizer()

        class ViewerBackend(FakeBackend):
            def compute_snapshot(self, config):
                visualizer.update(config, "/tmp/scene.xml", (np.zeros((0, 3)),) * 3)
                return super().compute_snapshot(config)

            def close(self):
                visualizer.close()

        channel = RTChannelEmulation(
            "FAU_scene", (0, 0, 1), (1, 0, 1), 64000,
            backend=ViewerBackend(), visualize=True,
        )
        state_path = visualizer._state_path
        tb = gr.top_block()
        tb.connect(blocks.vector_source_c([1], True), channel,
                   blocks.null_sink(gr.sizeof_gr_complex))
        try:
            tb.start()
        finally:
            tb.stop()
            tb.wait()
        process.terminate.assert_called_once_with()
        self.assertFalse(state_path.exists())
        self.assertFalse(hasattr(channel, "_mobility_server"))
        channel.stop()  # Explicit cleanup remains safe but is no longer required.
        process.terminate.assert_called_once_with()

    def test_direct_tap_stream_follows_position_updates_not_noise(self):
        """RT vectors come from the model, not the received samples or LS."""
        from gnuradio import blocks, gr
        import numpy as np

        class KnownBackend:
            def compute_snapshot(self, config):
                phase = 1j if config.tx_position[0] else 1
                return ChannelSnapshot(config.scene_path, (phase, 0j, phase * 0.1j, 0j), True)

        channel = RTChannelEmulation(
            "FAU_scene", (0, 0, 1), (1, 0, 1), 64000,
            l_min=0, l_max=3, backend=KnownBackend(), tap_output=True, tap_decimation=6400,
        )
        source = blocks.vector_source_c([0j], True)
        throttle = blocks.throttle(gr.sizeof_gr_complex, 64000)
        sink = blocks.vector_sink_c(4)
        tb = gr.top_block()
        tb.connect(source, throttle, channel, blocks.null_sink(gr.sizeof_gr_complex))
        tb.connect((channel, 1), sink)

        def wait_for_taps(expected):
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if len(sink.data()) >= 4 and np.allclose(sink.data()[-4:], expected):
                    return
                time.sleep(0.01)
            self.fail(f"RT vector did not update to {expected}: {sink.data()[-4:]}")

        tb.start()
        try:
            wait_for_taps([1, 0, 0.1j, 0])
            channel.set_noise_voltage(0.03)
            channel.set_tx_position((2, 0, 1))
            wait_for_taps([1j, 0, -0.1, 0])
            with self.assertLogs("rt_channel_emulation.block", "WARNING"):
                channel.set_sample_rate(32000)
            wait_for_taps([0j] * 4)
            channel.set_sample_rate(64000)
            wait_for_taps([1j, 0, -0.1, 0])
        finally:
            tb.stop()
            tb.wait()

    def test_invalid_tap_stream_dimensions_are_rejected(self):
        for settings in ({"tap_decimation": 0}, {"tap_decimation": 1.5},
                         {"l_min": 2, "l_max": 1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                RTChannelEmulation("FAU_scene", (0, 0, 1), (1, 0, 1), 64000,
                                   backend=FakeBackend(), tap_output=True, **settings)

    def test_message_output_publishes_initial_and_updated_reference(self):
        from gnuradio import blocks, gr
        import pmt

        channel = RTChannelEmulation(
            "FAU_scene", (0, 0, 1), (1, 0, 1), 1e6, backend=FakeBackend()
        )
        # Keep the scheduler alive while testing asynchronous update messages.
        # A finite source can finish before the test sends its updates.
        source = blocks.vector_source_c([1 + 0j] * 4096, True)
        sink = blocks.null_sink(gr.sizeof_gr_complex)
        messages = blocks.message_debug()
        flowgraph = gr.top_block()
        flowgraph.connect(source, channel, sink)
        flowgraph.msg_connect((channel, "channel_estimate"), (messages, "store"))
        flowgraph.start()
        try:
            deadline = time.monotonic() + 2
            while messages.num_messages() < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(messages.num_messages(), 1)
            channel.set_noise_voltage(0.01)
            channel.set_tx_position((2, 0, 1))
            while messages.num_messages() < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(messages.num_messages(), 3)
            first = pmt.to_python(messages.get_message(0))
            last = pmt.to_python(messages.get_message(2))
            self.assertEqual(first["revision"], 1)
            self.assertEqual(last["revision"], 3)
            self.assertEqual(last["tx_position"], [2, 0, 1])
            self.assertEqual(last["effective_taps"], [1 + 0j])
        finally:
            flowgraph.stop()
            flowgraph.wait()
            channel.stop()

    def test_block_instantiates_with_fake_backend(self):
        block = RTChannelEmulation(
            scene_path="FAU_scene",
            tx_position=(0, 0, 1),
            rx_position=(1, 0, 1),
            sample_rate=1e6,
            noise_voltage=0.0,
            backend=FakeBackend(),
        )
        self.assertIsNotNone(block.snapshot)
        self.assertFalse(hasattr(block, "_tx_gain"))
        self.assertFalse(hasattr(block, "_rx_gain"))
        self.assertEqual(block._channel_model.noise_voltage(), 0.0)
        block.stop()

    def test_external_amplitude_blocks_scale_samples_but_not_rt_taps(self):
        """External TX=10 and RX=2 give 20x amplitude, not 20x RT coefficients."""
        from gnuradio import blocks, gr

        # A streaming channel model buffers samples internally, so use enough
        # constant samples to reach steady state before checking the amplitude.
        source_samples = (1.0 + 0.0j,) * 4096
        source = blocks.vector_source_c(source_samples, False)
        channel = RTChannelEmulation(
            scene_path="FAU_scene",
            tx_position=(0, 0, 1),
            rx_position=(1, 0, 1),
            sample_rate=1e6,
            noise_voltage=0.0,
            backend=FakeBackend(),
        )
        sink = blocks.vector_sink_c()
        flowgraph = gr.top_block()
        flowgraph.connect(source, blocks.multiply_const_cc(10), channel,
                          blocks.multiply_const_cc(2), sink)
        flowgraph.run()
        self.assertEqual(channel.channel_estimate["taps"], [1 + 0j])

        output_samples = sink.data()
        self.assertGreater(len(output_samples), 0)
        for output in output_samples[-100:]:
            self.assertAlmostEqual(output.real, 20.0, places=5)
            self.assertAlmostEqual(output.imag, 0.0, places=5)

    def test_linear_noise_voltage_sets_complex_rms_noise(self):
        """Linear noise_voltage=0.1 gives complex power approximately 0.01."""
        from gnuradio import blocks, gr

        source_samples = (1.0 + 0.0j,) * 65536
        source = blocks.vector_source_c(source_samples, False)
        channel = RTChannelEmulation(
            scene_path="FAU_scene",
            tx_position=(0, 0, 1),
            rx_position=(1, 0, 1),
            sample_rate=1e6,
            noise_voltage=0.1,
            backend=FakeBackend(),
        )
        sink = blocks.vector_sink_c()
        flowgraph = gr.top_block()
        flowgraph.connect(source, channel, sink)
        flowgraph.run()

        # Ignore startup samples and measure the complex error around the
        # known clean value. A finite Gaussian-noise record is approximate.
        output_samples = sink.data()[100:]
        noise_power = sum(abs(value - 1.0) ** 2 for value in output_samples) / len(
            output_samples
        )
        self.assertAlmostEqual(math.sqrt(noise_power), 0.1, delta=0.004)

    def test_invalid_position_messages_preserve_model_and_next_update_works(self):
        import pmt

        backend = FakeBackend()
        channel = RTChannelEmulation("FAU_scene", [0, 0, 1], [1, 0, 1], 1e6,
                                     backend=backend)
        self.addCleanup(channel.stop)
        for value in ([1, 2], [float("nan"), 2, 3], {"tx_position": [1, 2, 3]}):
            with self.assertLogs("rt_channel_emulation.block", "WARNING"):
                channel._config_proxy._handle_tx_position(pmt.to_pmt(value))
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(channel.channel_estimate["tx_position"], [0, 0, 1])
        channel._config_proxy._handle_tx_position(pmt.init_f64vector(3, [2, 3, 1]))
        self.assertEqual(channel.channel_estimate["tx_position"], [2, 3, 1])
        self.assertEqual(channel.channel_estimate["rx_position"], [1, 0, 1])
        # A backend failure also leaves the applied reference unchanged.
        with patch.object(backend, "compute_snapshot", side_effect=RuntimeError("trace failed")):
            with self.assertLogs("rt_channel_emulation.block", "WARNING"):
                channel._config_proxy._handle_rx_position(pmt.to_pmt([8, 9, 1]))
        self.assertEqual(channel.channel_estimate["rx_position"], [1, 0, 1])

    def test_external_udp_sockets_route_sender_positions_to_taps_and_viewer(self):
        """Exercise the real sender -> Socket PDU -> hierarchical message ports."""
        from gnuradio import blocks, gr, network
        import numpy as np
        import pmt

        class PositionBackend(FakeBackend):
            def compute_snapshot(self, config):
                super().compute_snapshot(config)
                # An observable reference for both coordinates; no RT cost here.
                visualizer.update(config, "/tmp/scene.xml", (np.zeros((0, 3)),) * 3)
                return ChannelSnapshot(config.scene_path,
                                       (complex(config.tx_position[0], config.rx_position[0]),), True)

            def close(self):
                visualizer.close()

        from rt_channel_emulation.visualization import QtSceneVisualizer
        visualizer = QtSceneVisualizer()
        with patch("rt_channel_emulation.visualization.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            backend = PositionBackend()
            channel = RTChannelEmulation(
                "FAU_scene", [0, 0, 1], [1, 0, 1], 64000, visualize=True,
                l_min=0, l_max=0, tap_output=True, tap_decimation=640, backend=backend,
            )
        ports, receivers = [], []
        for _ in range(2):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as temporary:
                temporary.bind(("127.0.0.1", 0))
                port = temporary.getsockname()[1]
            ports.append(port)
            receivers.append(network.socket_pdu("UDP_SERVER", "127.0.0.1", str(port), 1024))
        taps = blocks.vector_sink_c()
        messages = blocks.message_debug()
        flowgraph = gr.top_block()
        flowgraph.connect(blocks.vector_source_c([1], True),
                          blocks.throttle(gr.sizeof_gr_complex, 64000), channel,
                          blocks.null_sink(gr.sizeof_gr_complex))
        flowgraph.connect((channel, 1), taps)
        flowgraph.msg_connect((receivers[0], "pdus"), (channel, "tx_position"))
        flowgraph.msg_connect((receivers[1], "pdus"), (channel, "rx_position"))
        flowgraph.msg_connect((channel, "channel_estimate"), (messages, "store"))
        state_path = visualizer._state_path
        flowgraph.start()
        try:
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts/position_source.py"),
                 "--tx-port", str(ports[0]), "--rx-port", str(ports[1]),
                 "--tx-start", "2", "3", "1", "--rx-start", "8", "9", "1",
                 "--tx-velocity", "1", "0", "0", "--rx-velocity", "-1", "0", "0",
                 "--rate-hz", "10", "--updates", "2"],
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            deadline = time.monotonic() + 4
            while (messages.num_messages() < 5 or not taps.data()
                   or abs(taps.data()[-1] - (2.1 + 7.9j)) > 1e-5):
                self.assertLess(time.monotonic(), deadline, "position inputs did not update taps")
                time.sleep(0.01)
            self.assertEqual(backend.calls[-1].tx_position, (2.1, 3.0, 1.0))
            self.assertEqual(backend.calls[-1].rx_position, (7.9, 9.0, 1.0))
            self.assertTrue(backend.calls[-1].visualize)
            reference = pmt.to_python(messages.get_message(messages.num_messages() - 1))
            self.assertEqual(reference["taps"], [2.1 + 7.9j])
            with np.load(state_path) as state:
                np.testing.assert_allclose(state["tx_position"], [2.1, 3, 1])
                np.testing.assert_allclose(state["rx_position"], [7.9, 9, 1])
        finally:
            flowgraph.stop()
            flowgraph.wait()
        self.assertFalse(state_path.exists())

    def test_removed_socket_parameters_are_rejected(self):
        for key in ("mobility_enabled", "mobility_host", "mobility_port"):
            with self.subTest(key=key), self.assertRaises(TypeError):
                RTChannelEmulation("FAU_scene", [0, 0, 1], [1, 0, 1], 1e6,
                                   backend=FakeBackend(), **{key: 1})

    def test_direct_message_sources_update_each_port_and_paired_config(self):
        """Use scheduled message delivery, not direct Python handler calls."""
        from gnuradio import blocks, gr
        import pmt

        for port, payload, tx, rx in (
            ("tx_position", pmt.init_f64vector(3, [2, 3, 1]), [2, 3, 1], [1, 0, 1]),
            ("rx_position", pmt.to_pmt([8, 9, 1]), [0, 0, 1], [8, 9, 1]),
            ("config", pmt.to_pmt({"tx_position": [2, 3, 1], "rx_position": [8, 9, 1]}),
             [2, 3, 1], [8, 9, 1]),
        ):
            with self.subTest(port=port):
                backend = FakeBackend()
                channel = RTChannelEmulation("FAU_scene", [0, 0, 1], [1, 0, 1], 64000,
                                             backend=backend)
                flowgraph = gr.top_block()
                flowgraph.connect(blocks.vector_source_c([1], True), channel,
                                  blocks.null_sink(gr.sizeof_gr_complex))
                flowgraph.msg_connect((blocks.message_strobe(payload, 20), "strobe"),
                                      (channel, port))
                flowgraph.start()
                try:
                    self.assertTrue(backend.updated.wait(timeout=2))
                    reference = channel.channel_estimate
                    self.assertEqual(reference["tx_position"], tx)
                    self.assertEqual(reference["rx_position"], rx)
                finally:
                    flowgraph.stop()
                    flowgraph.wait()


@unittest.skipUnless(importlib.util.find_spec("sionna") is not None, "Sionna is not installed")
class SionnaBackendIntegrationTest(unittest.TestCase):
    def test_backend_instantiates(self):
        backend = SionnaRTChannelBackend()
        self.assertIsNotNone(backend)

    @unittest.skipUnless(GNURADIO_AVAILABLE, "GNU Radio is not installed")
    def test_position_message_retraces_real_scene_on_scheduler_thread(self):
        """Moving RX from 10 m to 20 m separation halves free-space amplitude."""
        from gnuradio import blocks, gr
        import pmt

        channel = RTChannelEmulation(
            "LOS_empty", [-5, 0, 1], [5, 0, 1], 64000, max_depth=3, l_min=0, l_max=0,
        )
        initial_amplitude = abs(channel.snapshot.taps[0])
        flowgraph = gr.top_block()
        flowgraph.connect(blocks.vector_source_c([1], True),
                          blocks.throttle(gr.sizeof_gr_complex, 64000), channel,
                          blocks.null_sink(gr.sizeof_gr_complex))
        source = blocks.message_strobe(pmt.init_f64vector(3, [15, 0, 1]), 100)
        flowgraph.msg_connect((source, "strobe"), (channel, "rx_position"))
        flowgraph.start()
        try:
            deadline = time.monotonic() + 30
            while channel.channel_estimate["rx_position"] != [15, 0, 1]:
                self.assertLess(time.monotonic(), deadline, "Sionna position update timed out")
                time.sleep(0.01)
            self.assertGreater(initial_amplitude, 0)
            self.assertAlmostEqual(abs(channel.snapshot.taps[0]) / initial_amplitude, 0.5,
                                   delta=0.001)
        finally:
            flowgraph.stop()
            flowgraph.wait()
