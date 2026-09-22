from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib import resources
import logging
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np

LOGGER = logging.getLogger(__name__)

BUNDLED_SCENE_ALIASES = {
    "FAU_scene": "FAU_scene/FAU_scene.xml",
    "POWDER": "POWDER/POWDER_dense.xml",
    "AERPAW_scene": "AERPAW_scene/AERPAW.xml",
    "LOS_empty": "LOS_empty/LOS_empty.xml",
    "NLOS_box": "NLOS_box/NLOS_box.xml",
}


def parse_position(value: Any, field_name: str) -> tuple[float, float, float]:
    """Return a validated ``(x, y, z)`` position as floats.

    Args:
        value: Three numeric coordinates or their tuple-string representation.
        field_name: Input name used in validation errors.

    Returns:
        A three-element position tuple in the scene coordinate system.
    """
    if isinstance(value, str):
        value = ast.literal_eval(value)

    if not isinstance(value, Iterable):
        raise ValueError(f"{field_name} must be a 3-element coordinate")

    items = tuple(value)
    if len(items) != 3:
        raise ValueError(f"{field_name} must be a 3-element coordinate")

    try:
        position = tuple(float(component) for component in items)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain numeric values") from exc

    if not all(math.isfinite(component) for component in position):
        raise ValueError(f"{field_name} must contain finite values")
    return position


def parse_bool(value: Any, field_name: str) -> bool:
    """Return a Boolean from GRC, Python, or PMT-compatible values."""
    if isinstance(value, bool):
        return value
    if value in (0, 1, "0", "1"):
        return bool(int(value))
    if isinstance(value, str) and value.lower() in ("false", "true"):
        return value.lower() == "true"
    raise ValueError(f"{field_name} must be true or false")


@dataclass(frozen=True)
class SimulationConfig:
    """Inputs required to compute one static Sionna RT channel snapshot."""

    scene_path: str
    tx_position: tuple[float, float, float] | str
    rx_position: tuple[float, float, float] | str
    sample_rate: float
    carrier_frequency_hz: float = 2.4e9
    max_depth: int = 5
    noise_voltage: float = 0.0  # GNU Radio's linear complex RMS noise amplitude.
    frequency_offset: float = 0.0
    epsilon: float = 1.0
    l_min: int = -6
    l_max: int = 32
    tx_pattern: str = "iso"
    tx_polarization: str = "V"
    rx_pattern: str = "iso"
    rx_polarization: str = "V"
    seed: int = 42
    visualize: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "scene_path", str(self.scene_path))
        object.__setattr__(self, "tx_position", parse_position(self.tx_position, "tx_position"))
        object.__setattr__(self, "rx_position", parse_position(self.rx_position, "rx_position"))
        object.__setattr__(self, "sample_rate", float(self.sample_rate))
        object.__setattr__(self, "carrier_frequency_hz", float(self.carrier_frequency_hz))
        object.__setattr__(self, "max_depth", int(self.max_depth))
        object.__setattr__(self, "noise_voltage", float(self.noise_voltage))
        object.__setattr__(self, "frequency_offset", float(self.frequency_offset))
        object.__setattr__(self, "epsilon", float(self.epsilon))
        object.__setattr__(self, "l_min", int(self.l_min))
        object.__setattr__(self, "l_max", int(self.l_max))
        object.__setattr__(self, "tx_pattern", str(self.tx_pattern))
        object.__setattr__(self, "tx_polarization", str(self.tx_polarization))
        object.__setattr__(self, "rx_pattern", str(self.rx_pattern))
        object.__setattr__(self, "rx_polarization", str(self.rx_polarization))
        object.__setattr__(self, "seed", int(self.seed))
        object.__setattr__(self, "visualize", parse_bool(self.visualize, "visualize"))

        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if not math.isfinite(self.noise_voltage) or self.noise_voltage < 0:
            raise ValueError("noise_voltage must be finite and non-negative")
        if self.epsilon <= 0.0:
            raise ValueError("epsilon must be positive")
        if self.l_max < self.l_min:
            raise ValueError("l_max must be greater than or equal to l_min")


@dataclass(frozen=True)
class ChannelSnapshot:
    """Sionna output needed by the single-stream GNU Radio channel model."""

    resolved_scene_path: str
    taps: tuple[complex, ...]
    has_paths: bool


def bundled_scenes_root() -> Path:
    """Return the scene directory installed inside the Python package."""
    return Path(resources.files("rt_channel_emulation").joinpath("scenes"))


def resolve_scene_path(scene_path: str) -> str:
    """Resolve an existing path or a bundled scene alias."""
    candidate = Path(scene_path).expanduser()
    if candidate.exists():
        return str(candidate.resolve())

    bundled_relative_path = BUNDLED_SCENE_ALIASES.get(scene_path)
    if bundled_relative_path is not None:
        aliased_path = bundled_scenes_root() / bundled_relative_path
        if aliased_path.exists():
            return str(aliased_path.resolve())

    resolved = (bundled_scenes_root() / candidate).resolve()
    if resolved.exists():
        return str(resolved)

    raise FileNotFoundError(f"Scene path not found: {scene_path}")


def extract_single_link_taps(taps: Any) -> np.ndarray:
    """Flatten Sionna taps for one TX, one RX, and one antenna each.

    Sionna stores link, antenna, and time axes before the final tap axis. This
    block supports one element on every leading axis because GNU Radio exposes
    one complex input stream and one complex output stream.
    """
    tap_array = np.asarray(taps, dtype=np.complex128)
    if tap_array.ndim == 0:
        raise ValueError("taps must be at least 1-dimensional")
    if any(size != 1 for size in tap_array.shape[:-1]):
        raise ValueError("expected taps for one TX, one RX, one antenna, and one time step")
    return tap_array.reshape(-1)


class SionnaRTChannelBackend:
    """Convert a Sionna scene and link configuration into GNU Radio FIR taps."""

    def __init__(self, visualizer: Any = None) -> None:
        if visualizer is None:
            from .visualization import QtSceneVisualizer

            visualizer = QtSceneVisualizer()
        self._visualizer = visualizer

    def update_visualization(self, config: SimulationConfig) -> None:
        """Close the viewer when visualization is disabled at runtime."""
        self._visualizer.update(config, None, None)

    def close(self) -> None:
        """Close the visualization process started by this backend."""
        self._visualizer.close()

    def compute_snapshot(self, config: SimulationConfig) -> ChannelSnapshot:
        """Trace one static link and return its sampled complex channel taps."""
        from sionna.rt import PathSolver, PlanarArray, Receiver, Transmitter, load_scene

        resolved_scene_path = resolve_scene_path(config.scene_path)
        scene = load_scene(resolved_scene_path, merge_shapes=True)
        scene.frequency = config.carrier_frequency_hz
        scene.tx_array = PlanarArray(
            num_rows=1,
            num_cols=1,
            vertical_spacing=0.5,
            horizontal_spacing=0.5,
            pattern=config.tx_pattern,
            polarization=config.tx_polarization,
        )
        scene.rx_array = PlanarArray(
            num_rows=1,
            num_cols=1,
            vertical_spacing=0.5,
            horizontal_spacing=0.5,
            pattern=config.rx_pattern,
            polarization=config.rx_polarization,
        )

        for name in ("tx", "rx"):
            try:
                scene.remove(name)
            except Exception:
                pass

        tx = Transmitter(name="tx", position=config.tx_position, display_radius=0.5)
        rx = Receiver(name="rx", position=config.rx_position, display_radius=0.5)
        scene.add(tx)
        scene.add(rx)
        tx.look_at(rx)

        paths = PathSolver()(
            scene=scene,
            max_depth=config.max_depth,
            los=True,
            specular_reflection=True,
            diffuse_reflection=False,
            refraction=True,
            synthetic_array=False,
            seed=config.seed,
        )

        taps = paths.taps(
            bandwidth=config.sample_rate,
            l_min=config.l_min,
            l_max=config.l_max,
            sampling_frequency=None,
            normalize=False,
            normalize_delays=True,
            out_type="numpy",
        )

        flat_taps = extract_single_link_taps(taps)
        snapshot = ChannelSnapshot(
            resolved_scene_path=resolved_scene_path,
            taps=tuple(complex(value) for value in flat_taps.tolist()),
            has_paths=bool(np.any(np.isfinite(flat_taps) & (np.abs(flat_taps) > 0.0))),
        )

        path_segments = None
        if config.visualize:
            from sionna.rt import render

            rendered_segments = render.paths_to_segments(paths)
            if rendered_segments is None:
                empty = np.empty((0, 3), dtype=float)
                path_segments = (empty, empty.copy(), empty.copy())
            else:
                path_segments = tuple(np.asarray(values) for values in rendered_segments)

        LOGGER.info(
            "Computed snapshot for %s with %d taps, has_paths=%s",
            resolved_scene_path,
            len(snapshot.taps),
            snapshot.has_paths,
        )
        self._visualizer.update(config, resolved_scene_path, path_segments)
        return snapshot
