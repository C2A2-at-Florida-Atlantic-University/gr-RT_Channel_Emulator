from __future__ import annotations

import cmath
import json
import logging
from numbers import Real
from typing import Any

import numpy as np

from .backend import SimulationConfig, parse_bool, parse_position
from .runtime import ChannelModelController

try:
    from gnuradio import channels, gr
    import pmt

    GNURADIO_AVAILABLE = True
except ImportError as exc:
    GNURADIO_AVAILABLE = False
    _IMPORT_ERROR = exc


def _pmt_to_mapping(message: Any) -> dict[str, Any]:
    payload = pmt.to_python(message)
    if isinstance(payload, dict):
        return payload
    raise TypeError("config message must be a PMT dictionary")


def _pmt_to_position(message: Any, name: str) -> tuple[float, float, float]:
    """Read XYZ metres from a PMT vector or a Socket PDU's UTF-8 JSON array.

    A PDU is a (metadata, byte-vector) pair; metadata is not used here.
    Direct message sources can instead send pmt.init_f64vector(3, [x, y, z]).
    Both forms must contain exactly three finite real numbers, not GPS angles.
    """
    if pmt.is_pair(message):
        data = pmt.cdr(message)
        if not pmt.is_u8vector(data):
            raise ValueError(f"{name} PDU must contain UTF-8 JSON bytes")
        payload = json.loads(bytes(pmt.u8vector_elements(data)).decode("utf-8"))
    else:
        payload = pmt.to_python(message)
    if not isinstance(payload, (list, tuple, np.ndarray)) or len(payload) != 3:
        raise ValueError(f"{name} must be an [X, Y, Z] array")
    if any(not isinstance(value, Real) or isinstance(value, (bool, np.bool_))
           for value in payload):
        raise ValueError(f"{name} must contain real numbers")
    return parse_position(payload, name)


if GNURADIO_AVAILABLE:

    class _ConfigMessageProxy(gr.sync_block):
        """Bridge messages and own lifecycle hooks, paced by the IQ stream."""

        def __init__(self, controller, on_stop) -> None:
            gr.sync_block.__init__(self, name="rt_channel_emulation_config",
                                   in_sig=[np.complex64], out_sig=[])
            self.controller = controller
            self._on_stop = on_stop
            # GNU Radio looks handlers up by method name on this block, so use
            # its own named methods (not lambdas or another object's methods).
            for name, handler in (("config", self._handle_config),
                                  ("tx_position", self._handle_tx_position),
                                  ("rx_position", self._handle_rx_position)):
                self.message_port_register_in(pmt.intern(name))
                self.set_msg_handler(pmt.intern(name), handler)
            self.message_port_register_out(pmt.intern("channel_estimate"))

        def _handle_config(self, message: Any) -> None:
            self.controller.update(_pmt_to_mapping(message))

        def _handle_tx_position(self, message: Any) -> None:
            self._handle_position_message("tx_position", message)

        def _handle_rx_position(self, message: Any) -> None:
            self._handle_position_message("rx_position", message)

        def _handle_position_message(self, name: str, message: Any) -> None:
            """Update one node; malformed messages leave the current model intact."""
            try:
                position = _pmt_to_position(message, name)
                self.controller.update({name: position})
            except Exception as exc:
                # A bad packet or failed trace must not terminate the scheduler.
                logging.getLogger(__name__).warning("Ignoring %s update: %s", name, exc)

        def publish_estimate(self, estimate: dict[str, Any]) -> None:
            """Convert complex tap lists and metadata into a PMT dictionary."""
            self.message_port_pub(pmt.intern("channel_estimate"), pmt.to_pmt(estimate))

        def start(self) -> bool:
            # Constructor-time publication would precede external connections.
            # The internal basic block starts once those connections exist.
            with self.controller._lock:
                self.publish_estimate(self.controller.channel_estimate())
            return True

        def stop(self) -> bool:
            # GNU Radio schedules the internal blocks, not hier_block2.stop().
            return self._on_stop()

        def work(self, input_items, output_items):
            # A stream connection keeps this block scheduled even when all
            # external message ports are unused. No sample processing/copying.
            return len(input_items[0])

    class _TapVectorStream(gr.decim_block):
        """Emit the cached RT coefficients once per D received IQ samples.

        Input samples provide pacing only; their values never enter the RT
        reference. Each output item is an L-element complex64 vector. Updating
        the cache never traces rays or holds the controller lock in work().
        """

        def __init__(self, num_taps: int, decimation: int) -> None:
            gr.decim_block.__init__(
                self, name="rt_tap_vectors", in_sig=[np.complex64],
                out_sig=[(np.complex64, num_taps)], decim=decimation,
            )
            self.taps = np.zeros(num_taps, dtype=np.complex64)
            self.set_max_noutput_items(1)
            self.set_tag_propagation_policy(gr.TPP_DONT)

        def work(self, input_items, output_items):
            output_items[0][:] = self.taps
            return len(output_items[0])

    class RTChannelEmulation(gr.hier_block2):
        """Complex IQ channel, optional RT tap vectors, and reference messages."""

        def __init__(
            self,
            scene_path: str,
            tx_position: tuple[float, float, float] | str,
            rx_position: tuple[float, float, float] | str,
            sample_rate: float,
            carrier_frequency_hz: float = 2.4e9,
            max_depth: int = 5,
            noise_voltage: float = 0.0,
            frequency_offset: float = 0.0,
            epsilon: float = 1.0,
            l_min: int = -6,
            l_max: int = 32,
            tx_pattern: str = "iso",
            tx_polarization: str = "V",
            rx_pattern: str = "iso",
            rx_polarization: str = "V",
            seed: int = 42,
            visualize: bool = False,
            backend: Any = None,
            tap_output: bool = False,
            tap_decimation: int = 200000,
        ) -> None:
            tap_output = parse_bool(tap_output, "tap_output")
            num_taps = int(l_max) - int(l_min) + 1
            if num_taps < 1:
                raise ValueError("l_max must be greater than or equal to l_min")
            if not isinstance(tap_decimation, int) or tap_decimation < 1:
                raise ValueError("tap_decimation must be a positive integer")
            output_sizes = [gr.sizeof_gr_complex]
            if tap_output:
                output_sizes.append(gr.sizeof_gr_complex * num_taps)
            gr.hier_block2.__init__(
                self,
                "RT Channel Emulation",
                gr.io_signature(1, 1, gr.sizeof_gr_complex),
                gr.io_signaturev(len(output_sizes), len(output_sizes), output_sizes),
            )

            self._config = SimulationConfig(
                scene_path=scene_path,
                tx_position=tx_position,
                rx_position=rx_position,
                sample_rate=sample_rate,
                carrier_frequency_hz=carrier_frequency_hz,
                max_depth=max_depth,
                noise_voltage=noise_voltage,
                frequency_offset=frequency_offset,
                epsilon=epsilon,
                l_min=l_min,
                l_max=l_max,
                tx_pattern=tx_pattern,
                tx_polarization=tx_polarization,
                rx_pattern=rx_pattern,
                rx_polarization=rx_polarization,
                seed=seed,
                visualize=visualize,
            )

            # Amplitude control belongs to the surrounding flowgraph. Pass
            # noise_voltage through unchanged, as in GNU Radio's channel model.
            self._channel_model = channels.channel_model(
                noise_voltage=0.0,
                frequency_offset=self._config.frequency_offset,
                epsilon=self._config.epsilon,
                taps=[1.0 + 0.0j],
                noise_seed=0,
                block_tags=False,
            )
            self.connect((self, 0), (self._channel_model, 0), (self, 0))

            if backend is None:
                from .backend import SionnaRTChannelBackend

                backend = SionnaRTChannelBackend()

            self._controller = ChannelModelController(
                backend,
                self._channel_model,
                self._config,
            )
            self._controller.initialize()

            # Port dimensions are fixed when the flowgraph is built. The IQ
            # output remains port 0; optional port 1 carries propagation-only
            # RT taps, with no added noise or external amplitude scaling.
            self._tap_stream = None
            self._tap_dimensions = (num_taps, float(sample_rate), int(l_min))
            if tap_output:
                self._tap_stream = _TapVectorStream(num_taps, tap_decimation)
                self._update_tap_stream()
                self.connect((self._channel_model, 0), self._tap_stream, (self, 1))

            # Keep transport outside the emulator. These handlers share the
            # existing trace -> FIR taps -> reference/visualization update path.
            self._config_proxy = _ConfigMessageProxy(self._controller, self.stop)
            self.connect((self._channel_model, 0), self._config_proxy)
            for name in ("config", "tx_position", "rx_position"):
                self.message_port_register_hier_in(name)
                self.msg_connect((self, name), (self._config_proxy, name))
            self.message_port_register_hier_out("channel_estimate")
            self.msg_connect(
                (self._config_proxy, "channel_estimate"), (self, "channel_estimate")
            )
            self._controller.on_update = self._publish_estimate

        @property
        def snapshot(self):
            return self._controller.snapshot

        @property
        def channel_estimate(self) -> dict[str, Any]:
            """Read the same model reference carried by the message output."""
            return self._controller.channel_estimate()

        def get_effective_taps(self, num_taps: int, sample_rate: float, l_min: int) -> list[complex]:
            """Read the RT reference for a fixed-length FIR comparison.

            Inputs describe the fixed LS vector length, sample rate, and lag
            origin. Output is that many propagation-only complex RT coefficients.
            A mismatched reference clears the plot with zeros and a warning;
            it is never padded/truncated or fitted to the LS coefficients.
            This read does not retrace the scene. Updates are asynchronous.
            """
            if not isinstance(num_taps, int) or num_taps < 1:
                raise ValueError("num_taps must be a positive integer")
            reference = self.channel_estimate
            taps = reference["effective_taps"]
            if (len(taps) != num_taps or reference["sample_rate_hz"] != sample_rate
                    or reference["l_min"] != l_min or reference["frequency_offset"] != 0
                    or reference["epsilon"] != 1 or not all(map(cmath.isfinite, taps))):
                logging.getLogger(__name__).warning(
                    "RT reference cleared: expected %d taps at %g samples/s, l_min=%d, "
                    "CFO=0, epsilon=1; received %d taps. Keep comparison settings fixed.",
                    num_taps, sample_rate, l_min, len(taps),
                )
                return [0j] * num_taps
            return taps

        def _update_tap_stream(self) -> None:
            """Replace the whole cached vector after an applied model update."""
            if self._tap_stream is not None:
                self._tap_stream.taps = np.asarray(
                    self.get_effective_taps(*self._tap_dimensions), dtype=np.complex64
                )

        def _publish_estimate(self, estimate: dict[str, Any]) -> None:
            """Keep stream and message outputs tied to the applied RT model."""
            self._update_tap_stream()
            self._config_proxy.publish_estimate(estimate)

        def set_scene_path(self, value: str) -> None:
            self._controller.update({"scene_path": value})

        def set_tx_position(self, value: Any) -> None:
            self._controller.update({"tx_position": value})

        def set_rx_position(self, value: Any) -> None:
            self._controller.update({"rx_position": value})

        def set_sample_rate(self, value: float) -> None:
            self._controller.update({"sample_rate": value})

        def set_carrier_frequency_hz(self, value: float) -> None:
            self._controller.update({"carrier_frequency_hz": value})

        def set_max_depth(self, value: int) -> None:
            self._controller.update({"max_depth": value})

        def set_noise_voltage(self, value: float) -> None:
            """Set linear complex RMS noise amplitude; zero disables noise."""
            self._controller.update({"noise_voltage": value})

        def set_frequency_offset(self, value: float) -> None:
            self._controller.update({"frequency_offset": value})

        def set_epsilon(self, value: float) -> None:
            self._controller.update({"epsilon": value})

        def set_l_min(self, value: int) -> None:
            self._controller.update({"l_min": value})

        def set_l_max(self, value: int) -> None:
            self._controller.update({"l_max": value})

        def set_tx_pattern(self, value: str) -> None:
            self._controller.update({"tx_pattern": value})

        def set_tx_polarization(self, value: str) -> None:
            self._controller.update({"tx_polarization": value})

        def set_rx_pattern(self, value: str) -> None:
            self._controller.update({"rx_pattern": value})

        def set_rx_polarization(self, value: str) -> None:
            self._controller.update({"rx_polarization": value})

        def set_seed(self, value: int) -> None:
            self._controller.update({"seed": value})

        def set_visualize(self, value: bool) -> None:
            """Open or close the lightweight Qt scene and path viewer."""
            self._controller.update({"visualize": value})

        def stop(self) -> bool:
            """Close the owned GUI; external socket blocks own their lifecycles."""
            close_backend = getattr(self._controller.backend, "close", None)
            if close_backend is not None:
                close_backend()
            return True

else:

    class RTChannelEmulation:  # pragma: no cover - only used without GNU Radio
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("GNU Radio is required to use RTChannelEmulation") from _IMPORT_ERROR
