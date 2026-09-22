"""Check the visible GNU Radio LS stages against an independent dense solve."""

import ast
from pathlib import Path
import re
import sys
import time
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from rt_channel_emulation import GNURADIO_AVAILABLE, RTChannelEmulation
from rt_channel_emulation.backend import ChannelSnapshot
from rt_channel_emulation.estimation import glfsr_correlation_spectrum


def example_probe():
    """Read the single literal probe definition from GRC; do not duplicate it."""
    grc = (ROOT / "examples/channel_estimation_demo.grc").read_text()
    value = re.search(r"- name: glfsr\n.*?    value: ([^\n]+)", grc, re.DOTALL)
    return np.asarray(ast.literal_eval(value.group(1)), dtype=complex)


class PeriodicLSMathTest(unittest.TestCase):
    def test_example_has_only_one_custom_block_and_shared_vector_source(self):
        grc = (ROOT / "examples/channel_estimation_demo.grc").read_text()
        self.assertEqual(re.findall(r"^  id: (rt_channel_emulation_\w+)$", grc, re.MULTILINE),
                         ["rt_channel_emulation_rt_channel_emulation"])
        self.assertNotIn("digital_glfsr_source_x", grc)
        for removed in ("variable_function_probe", "zero_clock", "blocks_add_const_vxx"):
            self.assertNotIn(removed, grc)
        self.assertIn("[rt_channel, '1', rt_magnitude, '0']", grc)
        self.assertRegex(grc, r"(?m)^\s+vector: ['\"]?glfsr['\"]?$")
        self.assertEqual(len(example_probe()), 255)

    def test_glfsr_formula_matches_constrained_ls_in_noise(self):
        probe = example_probe()
        n = len(probe)
        rng = np.random.default_rng(42)
        for length in (1, 4, 39, n):
            with self.subTest(length=length):
                matrix = np.column_stack([np.roll(probe, j) for j in range(length)])
                gram = (n + 1) * np.eye(length) - np.ones((length, length))
                np.testing.assert_allclose(matrix.conj().T @ matrix, gram, atol=1e-12)
                h = rng.normal(size=length) + 1j * rng.normal(size=length)
                received = matrix @ h + 0.1 * (rng.normal(size=n) + 1j * rng.normal(size=n))
                weights = glfsr_correlation_spectrum(probe, length)
                # NumPy's IFFT already includes 1/N; GNU Radio's does not.
                r = (n * np.fft.ifft(np.fft.fft(received) * weights))[:length]
                estimated = (r + sum(r) / (n + 1 - length)) / (n + 1)
                expected = np.linalg.lstsq(matrix, received, rcond=None)[0]
                np.testing.assert_allclose(estimated, expected, atol=1e-11)

    def test_invalid_probe_and_dimensions_are_rejected(self):
        for probe, length, periods in (
            ([1, 1, 1], 2, 1), ([1, 0, -1], 2, 1), ([[1, -1]], 1, 1),
            ([float("nan")], 1, 1), (example_probe(), 256, 1),
            (example_probe(), 0, 1), (example_probe(), 39, 0),
        ):
            with self.subTest(length=length, periods=periods), self.assertRaises(ValueError):
                glfsr_correlation_spectrum(probe, length, periods)


@unittest.skipUnless(GNURADIO_AVAILABLE, "GNU Radio is not installed")
class StandardLSStreamingTest(unittest.TestCase):
    def test_standard_blocks_match_dense_ls_and_keep_rt_length(self):
        from gnuradio import blocks, fft, gr

        probe = example_probe()
        n = len(probe)
        for length, periods, keep in ((1, 1, 1), (4, 8, 3), (39, 8, 3)):
            with self.subTest(length=length, periods=periods):
                h = (np.arange(length) + 1) * (0.003 + 0.002j)

                class KnownBackend:
                    def compute_snapshot(self, config):
                        return ChannelSnapshot(config.scene_path, tuple(h), True)

                channel = RTChannelEmulation(
                    "FAU_scene", (0, 0, 1), (1, 0, 1), 64000,
                    l_min=-6, l_max=length - 7, noise_voltage=0.01,
                    backend=KnownBackend(),
                    tap_output=True, tap_decimation=n * periods * keep,
                )
                source = blocks.vector_source_c(probe.tolist(), True)
                head = blocks.head(gr.sizeof_gr_complex, n * periods * keep * 10 + 2*n)
                delay = blocks.delay(gr.sizeof_gr_complex, max(2, length) + 1)
                skip = blocks.skiphead(gr.sizeof_gr_complex, n)
                vectors = blocks.stream_to_vector(gr.sizeof_gr_complex, n)
                integrate = blocks.integrate_cc(periods, n)
                select = blocks.keep_one_in_n(gr.sizeof_gr_complex * n, keep)
                forward = fft.fft_vcc(n, True, [], False)
                weights = blocks.multiply_const_vcc(glfsr_correlation_spectrum(probe, length, periods))
                inverse = fft.fft_vcc(n, False, [], False)
                stream = blocks.vector_to_stream(gr.sizeof_gr_complex, n)
                lags = blocks.keep_m_in_n(gr.sizeof_gr_complex, length, n, 0)
                total = blocks.integrate_cc(length)
                correction = blocks.multiply_const_cc(1 / (n + 1 - length))
                repeat = blocks.repeat(gr.sizeof_gr_complex, length)
                add = blocks.add_cc()
                normalize = blocks.multiply_const_cc(1 / (n + 1))
                taps = blocks.stream_to_vector(gr.sizeof_gr_complex, length)
                sink = blocks.vector_sink_c(length)
                observations = blocks.vector_sink_c(n)
                reference_sink = blocks.vector_sink_c(length)
                tb = gr.top_block()
                # External amplitudes are removed before LS, just as in the demo.
                tb.connect(source, head, blocks.multiply_const_cc(2), channel,
                           blocks.multiply_const_cc(3), blocks.multiply_const_cc(1/6),
                           delay, skip, vectors, integrate, select)
                tb.connect(select, observations)
                tb.connect(select, forward, weights, inverse, stream, lags, (add, 0))
                tb.connect(lags, total, correction, repeat, (add, 1))
                tb.connect(add, normalize, taps, sink)
                tb.connect((channel, 1), reference_sink)
                tb.start()
                try:
                    deadline = time.monotonic() + 5
                    while min(len(sink.data()), len(reference_sink.data())) < 8*length:
                        self.assertLess(time.monotonic(), deadline, "standard LS pipeline stalled")
                        time.sleep(0.01)
                    estimated = np.array(sink.data()).reshape(-1, length)[:8]
                    received = np.array(observations.data()).reshape(-1, n)[:8] / periods
                    matrix = np.column_stack([np.roll(probe, j) for j in range(length)])
                    expected = np.linalg.lstsq(matrix, received.T, rcond=None)[0].T
                    np.testing.assert_allclose(estimated, expected, atol=2e-6)
                    # The original complex gain/phase and edge taps are retained.
                    np.testing.assert_allclose(estimated.mean(axis=0), h, atol=0.001)
                    rt = np.array(reference_sink.data()).reshape(-1, length)
                    self.assertEqual(estimated.shape[1], rt.shape[1])
                    self.assertEqual(len(channel.channel_estimate["effective_taps"]), length)
                    np.testing.assert_allclose(rt[-1], h, atol=1e-6)
                finally:
                    tb.stop()
                    tb.wait()
                    channel.stop()

    def test_reference_getter_follows_updates_and_rejects_mismatches(self):
        class KnownBackend:
            def compute_snapshot(self, config):
                phase = 1j if config.tx_position[0] else 1
                taps = tuple(phase * h for h in (1 + 0j, 0j, 0.1j, 0j))
                return ChannelSnapshot(config.scene_path, taps, True)

        channel = RTChannelEmulation(
            "FAU_scene", (0, 0, 1), (1, 0, 1), 64000,
            l_min=0, l_max=3, backend=KnownBackend(),
        )
        try:
            np.testing.assert_allclose(channel.get_effective_taps(4, 64000, 0), [1, 0, 0.1j, 0])
            channel.set_noise_voltage(0.03)
            channel.set_tx_position((2, 0, 1))
            np.testing.assert_allclose(channel.get_effective_taps(4, 64000, 0), [1j, 0, -0.1, 0])
            for length, rate, lag in ((3, 64000, 0), (5, 64000, 0), (4, 1e6, 0), (4, 64000, -6)):
                with self.assertLogs("rt_channel_emulation.block", "WARNING"):
                    self.assertEqual(channel.get_effective_taps(length, rate, lag), [0j]*length)
            channel.set_frequency_offset(0.1)
            with self.assertLogs("rt_channel_emulation.block", "WARNING"):
                self.assertEqual(channel.get_effective_taps(4, 64000, 0), [0j]*4)
            channel.set_frequency_offset(0)
            self.assertEqual(len(channel.get_effective_taps(4, 64000, 0)), 4)
        finally:
            channel.stop()


if __name__ == "__main__":
    unittest.main()
