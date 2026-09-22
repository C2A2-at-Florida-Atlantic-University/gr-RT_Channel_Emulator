"""Lifecycle and state transport for the lightweight Qt scene viewer."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

import numpy as np

LOGGER = logging.getLogger(__name__)


def build_gui_command(scene_path: str, state_path: str) -> list[str]:
    """Return the shell-free command for the bundled Qt viewer."""
    launcher = Path(__file__).with_name("gui_launcher.py")
    return [
        sys.executable,
        str(launcher),
        "--scene",
        scene_path,
        "--state",
        state_path,
        "--parent-pid",
        str(os.getpid()),
    ]


class QtSceneVisualizer:
    """Keep one viewer alive while TX, RX, and propagation paths change.

    The scene XML and PLY meshes are static and are loaded once by the child
    process. Each update atomically replaces one small NumPy state file. The
    viewer polls that file, so mobility never restarts or reloads the scene.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._scene_path: str | None = None
        self._state_directory: Path | None = None
        self._state_path: Path | None = None

    def update(
        self,
        config: Any,
        scene_path: str | None,
        path_segments: tuple[np.ndarray, np.ndarray, np.ndarray] | None,
    ) -> None:
        """Show, update, or close the viewer to match ``config.visualize``.

        ``scene_path`` identifies the static Mitsuba XML model. Each array in
        ``path_segments`` has shape ``(N, 3)`` and contains segment starts,
        ends, or RGB colors. The method has no data return value.
        """
        if not config.visualize:
            self.close()
            return
        if scene_path is None or path_segments is None:
            raise ValueError(
                "scene path and path segments are required when visualization is enabled"
            )

        resolved_scene_path = str(Path(scene_path).resolve())
        if self._scene_path not in (None, resolved_scene_path):
            # Scene geometry is static inside the viewer, so only a scene-file
            # change requires a restart. Mobility updates do not reach here.
            self.close()

        if self._state_path is None:
            self._state_directory = Path(
                tempfile.mkdtemp(prefix="rt-channel-viewer-")
            )
            self._state_path = self._state_directory / "state.npz"

        self._write_state(config, path_segments)
        self._scene_path = resolved_scene_path

        if self._process is not None and self._process.poll() is None:
            return

        command = build_gui_command(resolved_scene_path, str(self._state_path))
        LOGGER.info("Starting lightweight Qt scene viewer for %s", resolved_scene_path)
        try:
            self._process = subprocess.Popen(command, env=os.environ.copy())
        except Exception:
            self.close()
            raise

    def _write_state(
        self,
        config: Any,
        path_segments: tuple[np.ndarray, np.ndarray, np.ndarray],
    ) -> None:
        """Atomically publish one complete visualization snapshot."""
        if self._state_path is None:
            raise RuntimeError("visualization state path is not initialized")

        temporary_path = self._state_path.with_suffix(".next.npz")
        with temporary_path.open("wb") as state_file:
            np.savez(
                state_file,
                starts=np.asarray(path_segments[0], dtype=float),
                ends=np.asarray(path_segments[1], dtype=float),
                colors=np.asarray(path_segments[2], dtype=float),
                tx_position=np.asarray(config.tx_position, dtype=float),
                rx_position=np.asarray(config.rx_position, dtype=float),
            )
        os.replace(temporary_path, self._state_path)

    def close(self) -> None:
        """Terminate the owned viewer and remove its temporary state."""
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        if self._state_directory is not None:
            shutil.rmtree(self._state_directory, ignore_errors=True)
        self._scene_path = None
        self._state_directory = None
        self._state_path = None
