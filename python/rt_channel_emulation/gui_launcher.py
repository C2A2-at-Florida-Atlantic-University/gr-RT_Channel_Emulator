"""Display a Mitsuba PLY scene, TX/RX positions, and Sionna path segments."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np

_PLY_TYPES = {
    "char": "b",
    "int8": "b",
    "uchar": "B",
    "uint8": "B",
    "short": "h",
    "int16": "h",
    "ushort": "H",
    "uint16": "H",
    "int": "i",
    "int32": "i",
    "uint": "I",
    "uint32": "I",
    "float": "f",
    "float32": "f",
    "double": "d",
    "float64": "d",
}

# Display-only colors for Sionna's built-in ITU radio-material types. The
# colors are intentionally fixed so the same material looks the same in every
# scene and every viewer run. They do not change the ray-tracing calculation.
ITU_MATERIAL_COLORS = {
    "itu_vacuum": (167, 216, 255),
    "itu_concrete": (156, 163, 175),
    "itu_brick": (196, 90, 61),
    "itu_plasterboard": (227, 220, 200),
    "itu_wood": (139, 90, 43),
    "itu_glass": (79, 195, 232),
    "itu_ceiling_board": (240, 230, 194),
    "itu_chipboard": (181, 132, 74),
    "itu_plywood": (208, 161, 91),
    "itu_marble": (216, 222, 233),
    "itu_floorboard": (111, 68, 37),
    "itu_metal": (143, 169, 189),
    "itu_very_dry_ground": (214, 181, 109),
    "itu_medium_dry_ground": (154, 107, 63),
    "itu_wet_ground": (78, 116, 73),
}
UNKNOWN_MATERIAL_COLOR = (120, 131, 143)

# Faces were already fully opaque. Reduce RGB brightness for stronger contrast;
# edges and vertex dots retain the material's hue at a still darker shade.
MATERIAL_FILL_BRIGHTNESS = 0.85
MATERIAL_EDGE_BRIGHTNESS = 0.50
VERTEX_RADIUS = 0.15  # Projected scene metres; vertex dots zoom with the mesh.

# Display-only camera and light settings. Angles are in degrees; the minimum
# view span is in projected scene metres and prevents extreme zoom at short links.
CAMERA_ELEVATION_DEG = 20.0
CAMERA_AZIMUTH_DEG = 45.0
MIN_VIEW_SPAN = 40.0
VIEW_PADDING = 2.0
LIGHT_DIRECTION = np.array((-0.6, -0.8, 1.0))  # From surface toward the light.

def camera_axes(elevation_deg=CAMERA_ELEVATION_DEG,
                azimuth_deg=CAMERA_AZIMUTH_DEG) -> np.ndarray:
    """Return unit camera axes: horizontal, vertical, and toward the viewer.

    Elevation is the angle above the XY ground plane; azimuth rotates around Z.
    The same axes must project mesh faces, paths, nodes, shadows, and depth.
    """
    azimuth = np.deg2rad(azimuth_deg)
    elevation = np.deg2rad(elevation_deg)
    return np.array([
        (-np.sin(azimuth), np.cos(azimuth), 0.0),
        (-np.sin(elevation) * np.cos(azimuth),
         -np.sin(elevation) * np.sin(azimuth), np.cos(elevation)),
        (np.cos(elevation) * np.cos(azimuth),
         np.cos(elevation) * np.sin(azimuth), np.sin(elevation)),
    ])


_VIEW_AXES = camera_axes()

# Dynamic-overlay colors in normalized RGB. The path values match the colors
# returned by Sionna RT 2.0.1's paths_to_segments() helper for the interaction
# types enabled by this block.
NODE_COLORS = {
    "TX node": (1.0, 0.0, 0.0),
    "RX node": (0.0, 1.0, 1.0),
}
PATH_COLORS = {
    "LoS / initial segment": (0.5, 0.5, 0.5),
    "Specular reflection": (0.6, 0.6, 1.0),
    "Refraction / transmission": (1.0, 0.6, 0.6),
}


@dataclass(frozen=True)
class SceneGeometry:
    """Static scene faces, edges, and their display-only material colors.

    ``edges`` has shape ``(E, 2, 3)``: E line segments, two XYZ endpoints.
    ``colors`` has shape ``(E, 3)`` and stores one 8-bit RGB color per edge.
    Each item in ``polygons`` has shape ``(V, 3)`` for V XYZ face vertices.
    ``polygon_colors`` stores one 8-bit RGB color per polygon.
    ``materials`` contains the unique ``(name, RGB)`` pairs used by the scene.
    """

    edges: np.ndarray
    colors: np.ndarray
    polygons: tuple[np.ndarray, ...]
    polygon_colors: np.ndarray
    materials: tuple[tuple[str, tuple[int, int, int]], ...]


@dataclass(frozen=True)
class ViewerState:
    """Dynamic inputs drawn over the cached scene.

    ``starts[i]`` and ``ends[i]`` are the XYZ endpoints of segment ``i``.
    ``colors[i]`` is its RGB color in the range 0 to 1. TX and RX positions
    are three-element XYZ vectors in the scene coordinate system.
    """

    starts: np.ndarray
    ends: np.ndarray
    colors: np.ndarray
    tx_position: np.ndarray
    rx_position: np.ndarray


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True, help="Mitsuba scene XML file")
    parser.add_argument("--state", required=True, help="live NumPy state file")
    parser.add_argument("--parent-pid", type=int, help="exit when the flowgraph process exits")
    return parser


def _read_header(file) -> tuple[str, int, int, list[tuple], list[tuple]]:
    """Read the PLY format, counts, and ordered vertex/face properties."""
    if file.readline().strip() != b"ply":
        raise ValueError("not a PLY file")

    data_format = ""
    vertex_count = 0
    face_count = 0
    element = ""
    vertex_properties: list[tuple] = []
    face_properties: list[tuple] = []

    while True:
        raw_line = file.readline()
        if not raw_line:
            raise ValueError("PLY header has no end_header line")
        words = raw_line.decode("ascii").strip().split()
        if not words or words[0] in ("comment", "obj_info"):
            continue
        if words[0] == "end_header":
            break
        if words[0] == "format":
            data_format = words[1]
        elif words[0] == "element":
            element = words[1]
            if element == "vertex":
                vertex_count = int(words[2])
            elif element == "face":
                face_count = int(words[2])
        elif words[0] == "property":
            if words[1] == "list":
                description = ("list", words[2], words[3], words[4])
            else:
                description = ("scalar", words[1], words[2])
            if element == "vertex":
                vertex_properties.append(description)
            elif element == "face":
                face_properties.append(description)

    if data_format not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise ValueError(f"unsupported PLY format: {data_format}")
    if any(item[0] != "scalar" for item in vertex_properties):
        raise ValueError("list-valued PLY vertex properties are not supported")
    return data_format, vertex_count, face_count, vertex_properties, face_properties


def _unpack(file, endian: str, value_type: str):
    """Read one typed scalar from a binary PLY stream."""
    try:
        value_struct = struct.Struct(endian + _PLY_TYPES[value_type])
    except KeyError as exc:
        raise ValueError(f"unsupported PLY property type: {value_type}") from exc
    data = file.read(value_struct.size)
    if len(data) != value_struct.size:
        raise ValueError("unexpected end of PLY data")
    return value_struct.unpack(data)[0]


def _read_binary_records(
    file,
    endian: str,
    vertex_count: int,
    face_count: int,
    vertex_properties: list[tuple],
    face_properties: list[tuple],
) -> tuple[np.ndarray, list[list[int]]]:
    """Read binary PLY vertices and polygon indices in declared order."""
    vertex_names = [item[2] for item in vertex_properties]
    try:
        coordinate_indices = [vertex_names.index(axis) for axis in ("x", "y", "z")]
    except ValueError as exc:
        raise ValueError("PLY vertices must contain x, y, and z") from exc

    vertices = np.empty((vertex_count, 3), dtype=float)
    for row in range(vertex_count):
        values = [_unpack(file, endian, item[1]) for item in vertex_properties]
        vertices[row] = [values[index] for index in coordinate_indices]

    faces: list[list[int]] = []
    for _ in range(face_count):
        vertex_indices: list[int] | None = None
        for item in face_properties:
            if item[0] == "scalar":
                _unpack(file, endian, item[1])
                continue
            count = int(_unpack(file, endian, item[1]))
            values = [int(_unpack(file, endian, item[2])) for _ in range(count)]
            if item[3] in ("vertex_indices", "vertex_index"):
                vertex_indices = values
        if vertex_indices is not None:
            faces.append(vertex_indices)
    return vertices, faces


def _read_ascii_records(
    file,
    vertex_count: int,
    face_count: int,
    vertex_properties: list[tuple],
    face_properties: list[tuple],
) -> tuple[np.ndarray, list[list[int]]]:
    """Read ASCII PLY vertices and polygon indices in declared order."""
    vertex_names = [item[2] for item in vertex_properties]
    try:
        coordinate_indices = [vertex_names.index(axis) for axis in ("x", "y", "z")]
    except ValueError as exc:
        raise ValueError("PLY vertices must contain x, y, and z") from exc

    vertices = np.empty((vertex_count, 3), dtype=float)
    for row in range(vertex_count):
        values = file.readline().decode("ascii").split()
        vertices[row] = [float(values[index]) for index in coordinate_indices]

    faces: list[list[int]] = []
    for _ in range(face_count):
        values = file.readline().decode("ascii").split()
        offset = 0
        vertex_indices: list[int] | None = None
        for item in face_properties:
            if item[0] == "scalar":
                offset += 1
                continue
            count = int(values[offset])
            offset += 1
            entries = [int(value) for value in values[offset : offset + count]]
            offset += count
            if item[3] in ("vertex_indices", "vertex_index"):
                vertex_indices = entries
        if vertex_indices is not None:
            faces.append(vertex_indices)
    return vertices, faces


def load_ply_mesh(path: str | Path) -> tuple[np.ndarray, list[list[int]]]:
    """Return the XYZ vertices and polygon vertex indices from one PLY file."""
    path = Path(path)
    with path.open("rb") as file:
        data_format, vertex_count, face_count, vertex_properties, face_properties = (
            _read_header(file)
        )
        if data_format == "ascii":
            vertices, faces = _read_ascii_records(
                file,
                vertex_count,
                face_count,
                vertex_properties,
                face_properties,
            )
        else:
            endian = "<" if data_format == "binary_little_endian" else ">"
            vertices, faces = _read_binary_records(
                file,
                endian,
                vertex_count,
                face_count,
                vertex_properties,
                face_properties,
            )

    for face in faces:
        if any(index < 0 or index >= len(vertices) for index in face):
            raise ValueError(f"PLY face contains an invalid vertex index: {path}")
    return vertices, faces


def _mesh_edges(vertices: np.ndarray, faces: list[list[int]]) -> np.ndarray:
    """Convert polygon faces to boundary edges without re-reading the PLY file."""
    edges = []
    for face in faces:
        if len(face) < 2:
            continue
        for first, second in zip(face, face[1:] + face[:1]):
            edges.append((vertices[first], vertices[second]))
    return np.asarray(edges, dtype=float).reshape(-1, 2, 3)


def load_ply_edges(path: str | Path) -> np.ndarray:
    """Return polygon boundary edges with shape ``(edge, endpoint, xyz)``."""
    vertices, faces = load_ply_mesh(path)
    return _mesh_edges(vertices, faces)


def canonical_material_name(reference_id: str | None) -> str:
    """Normalize a Mitsuba BSDF reference to Sionna's ``itu_name`` form."""
    if not reference_id:
        return "unassigned"
    name = reference_id.strip().lower()
    if name.startswith("mat-"):
        name = name[4:]
    return name.replace("-", "_")


def load_scene_geometry(scene_path: str | Path) -> SceneGeometry:
    """Load colored PLY faces, or empty geometry for a free-space scene."""
    scene_path = Path(scene_path).resolve()
    root = ET.parse(scene_path).getroot()
    meshes = []
    mesh_colors = []
    polygons = []
    polygon_colors = []
    materials: dict[str, tuple[int, int, int]] = {}
    for shape in root.findall(".//shape[@type='ply']"):
        if shape.find("./transform") is not None:
            raise ValueError("the lightweight viewer does not support shape transforms")
        filename = shape.find("./string[@name='filename']")
        if filename is not None:
            vertices, faces = load_ply_mesh(
                scene_path.parent / filename.attrib["value"]
            )
            edges = _mesh_edges(vertices, faces)
            reference = shape.find("./ref[@name='bsdf']")
            reference_id = reference.attrib.get("id") if reference is not None else None
            material = canonical_material_name(reference_id)
            color = ITU_MATERIAL_COLORS.get(material, UNKNOWN_MATERIAL_COLOR)
            meshes.append(edges)
            mesh_colors.append(np.tile(color, (len(edges), 1)))
            shape_polygons = [vertices[face] for face in faces if len(face) >= 3]
            polygons.extend(shape_polygons)
            polygon_colors.extend([color] * len(shape_polygons))
            materials.setdefault(material, color)
    if not meshes and root.findall(".//shape"):
        raise ValueError(f"scene contains no PLY shapes: {scene_path}")
    return SceneGeometry(
        edges=np.concatenate(meshes, axis=0) if meshes else np.empty((0, 2, 3)),
        colors=(np.concatenate(mesh_colors, axis=0).astype(np.uint8)
                if meshes else np.empty((0, 3), dtype=np.uint8)),
        polygons=tuple(polygons),
        polygon_colors=np.asarray(polygon_colors, dtype=np.uint8).reshape(-1, 3),
        materials=tuple(materials.items()),
    )


def load_state(state_path: str | Path) -> ViewerState:
    """Read and validate one complete dynamic visualization snapshot."""
    with np.load(state_path, allow_pickle=False) as data:
        state = ViewerState(
            starts=np.asarray(data["starts"], dtype=float).copy(),
            ends=np.asarray(data["ends"], dtype=float).copy(),
            colors=np.asarray(data["colors"], dtype=float).copy(),
            tx_position=np.asarray(data["tx_position"], dtype=float).copy(),
            rx_position=np.asarray(data["rx_position"], dtype=float).copy(),
        )
    if state.starts.shape != state.ends.shape or state.starts.ndim != 2:
        raise ValueError("path starts and ends must have matching (N, 3) shapes")
    if state.starts.shape[1:] != (3,):
        raise ValueError("path starts and ends must have matching (N, 3) shapes")
    if state.colors.shape != state.starts.shape:
        raise ValueError("path colors must have shape (N, 3)")
    if state.tx_position.shape != (3,) or state.rx_position.shape != (3,):
        raise ValueError("TX and RX positions must each have shape (3,)")
    return state


def project_points(points: np.ndarray, axes=_VIEW_AXES) -> np.ndarray:
    """Project XYZ points to horizontal/vertical scene metres, without perspective."""
    return np.asarray(points, dtype=float) @ axes[:2].T


def view_depth(points: np.ndarray, axes=_VIEW_AXES) -> np.ndarray:
    """Return depth in scene metres; larger values are nearer the camera."""
    return np.asarray(points, dtype=float) @ axes[2]


def fit_node_view(tx, rx, viewport_size, axes=_VIEW_AXES) -> tuple[np.ndarray, float]:
    """Return the projected midpoint and pixels/metre scale fitting both nodes.

    TX/RX are XYZ positions; viewport_size is available width/height in pixels.
    Padding leaves space around the link. A minimum span also handles coincident
    nodes or nodes aligned along the camera direction without division by zero.
    """
    nodes = project_points(np.asarray((tx, rx), dtype=float), axes)
    center = nodes.mean(axis=0)
    span = np.maximum(np.ptp(nodes, axis=0) * VIEW_PADDING, MIN_VIEW_SPAN)
    scale = float(np.min(np.maximum(viewport_size, 1.0) / span))
    return center, scale


def ground_shadow(points: np.ndarray, ground_z: float) -> np.ndarray:
    """Project XYZ vertices away from the light onto the plane z=ground_z.

    For p - t*light, t=(p.z-ground_z)/light.z. Coordinates stay in scene
    metres. This flat-ground visual cue is independent of RF ray tracing.
    """
    points = np.asarray(points, dtype=float)
    distance = (points[..., 2] - ground_z) / LIGHT_DIRECTION[2]
    return points - distance[..., None] * LIGHT_DIRECTION


def material_shade(color, brightness: float) -> tuple[int, int, int]:
    """Scale an 8-bit material RGB triplet; opacity remains fully opaque."""
    return tuple(int(round(component * brightness)) for component in color)


def _run_qt(scene_path: str, state_path: str, parent_pid: int | None = None) -> int:
    """Create the Qt application; imports stay optional until visualization runs."""
    from PyQt5 import QtCore, QtGui, QtWidgets

    scene = load_scene_geometry(scene_path)

    class SceneView(QtWidgets.QWidget):
        """A cached filled scene with inexpensive dynamic overlays."""

        def __init__(self) -> None:
            super().__init__()
            sidebar_top = 96 + 20 * (len(scene.materials) + len(NODE_COLORS) + len(PATH_COLORS))
            self.setMinimumSize(800, max(600, sidebar_top + 230))
            self.setWindowTitle("GR RT Channel Emulation — Scene Viewer")
            self._state = load_state(state_path)
            self._state_mtime_ns = 0
            self._zoom_factor = 1.0
            self._view_axes = camera_axes()
            self._pan_offset = np.zeros(3)  # World metres relative to the TX/RX midpoint.
            self._drag_position = None
            self._cache_scene()

            # Native Qt controls stay outside the map and work without a mouse wheel.
            self._zoom_controls = QtWidgets.QWidget(self)
            layout = QtWidgets.QHBoxLayout(self._zoom_controls)
            layout.setContentsMargins(0, 0, 0, 0)
            zoom_out = QtWidgets.QPushButton("−")
            zoom_out.setToolTip("Zoom out")
            zoom_out.clicked.connect(lambda: self._change_zoom(-1))
            zoom_in = QtWidgets.QPushButton("+")
            zoom_in.setToolTip("Zoom in")
            zoom_in.clicked.connect(lambda: self._change_zoom(1))
            reset = QtWidgets.QPushButton("Fit TX/RX")
            reset.setToolTip("Reset to automatic framing of both nodes")
            reset.clicked.connect(self._fit_nodes)
            self._zoom_label = QtWidgets.QLabel("100%")
            self._zoom_label.setStyleSheet("color: #e1e1e1;")
            for button in (zoom_out, zoom_in):
                button.setFixedWidth(30)
            for widget in (zoom_out, zoom_in, self._zoom_label, reset):
                layout.addWidget(widget)

            self._camera_controls = QtWidgets.QWidget(self)
            controls = QtWidgets.QVBoxLayout(self._camera_controls)
            controls.setContentsMargins(0, 0, 0, 0)
            arrows = QtWidgets.QGridLayout()
            for text, row, column, dx, dy in (
                ("↑", 0, 1, 0, 60), ("←", 1, 0, -60, 0),
                ("→", 1, 2, 60, 0), ("↓", 2, 1, 0, -60),
            ):
                button = QtWidgets.QPushButton(text)
                button.setToolTip("Move the camera in this screen direction")
                button.clicked.connect(lambda checked=False, x=dx, y=dy: self._pan_camera(x, y))
                arrows.addWidget(button, row, column)
            controls.addLayout(arrows)
            angles = QtWidgets.QFormLayout()
            self._elevation = QtWidgets.QSpinBox()
            self._elevation.setRange(5, 85)
            self._elevation.setValue(int(CAMERA_ELEVATION_DEG))
            self._azimuth = QtWidgets.QSpinBox()
            self._azimuth.setRange(-180, 180)
            self._azimuth.setValue(int(CAMERA_AZIMUTH_DEG))
            for label, spin in (("Elevation", self._elevation), ("Rotation", self._azimuth)):
                spin.setSuffix("°")
                spin.setSingleStep(5)
                spin.setKeyboardTracking(False)  # Rebuild after typed angles are committed.
                spin.valueChanged.connect(self._change_angle)
                caption = QtWidgets.QLabel(label)
                caption.setStyleSheet("color: #e1e1e1;")
                angles.addRow(caption, spin)
            controls.addLayout(angles)

            self._timer = QtCore.QTimer(self)
            self._timer.timeout.connect(self._refresh_state)
            self._timer.start(100)

        def resizeEvent(self, event) -> None:
            # Place controls below both legends, using the same material row count.
            top = 96 + 20 * (len(scene.materials) + len(NODE_COLORS) + len(PATH_COLORS))
            self._zoom_controls.setGeometry(self.width() - 252, top, 240, 32)
            self._camera_controls.setGeometry(self.width() - 252, top + 40, 240, 174)
            super().resizeEvent(event)

        def _set_zoom(self, factor: float) -> None:
            """Set magnification relative to automatic TX/RX framing (10–800%)."""
            self._zoom_factor = float(np.clip(factor, 0.1, 8.0))
            self._zoom_label.setText(f"{self._zoom_factor * 100:.0f}%")
            self.update()

        def _change_zoom(self, steps: float) -> None:
            """Each wheel notch/button click multiplies magnification by 1.2."""
            self._set_zoom(self._zoom_factor * 1.2 ** float(np.clip(steps, -10, 10)))

        def _fit_nodes(self) -> None:
            """Recenter on both nodes and reset zoom, keeping the chosen angle."""
            self._pan_offset[:] = 0
            self._set_zoom(1.0)

        def _pan_camera(self, dx: float, dy: float) -> None:
            """Move the camera by pixels: positive dx=right, positive dy=up."""
            self._pan_offset += (dx * self._view_axes[0] + dy * self._view_axes[1]) / self._camera_scale
            self.update()

        def _change_angle(self) -> None:
            """Reproject cached mesh data and recalculate face order for this view."""
            self._view_axes = camera_axes(self._elevation.value(), self._azimuth.value())
            self._cache_scene()
            self.update()

        def mousePressEvent(self, event) -> None:
            if event.button() == QtCore.Qt.LeftButton and self._viewport().contains(event.localPos()):
                self._drag_position = event.localPos()
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                event.accept()
            else:
                super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:
            if self._drag_position is not None:
                delta = event.localPos() - self._drag_position
                self._drag_position = event.localPos()
                self._pan_camera(-delta.x(), delta.y())  # Map follows the dragging hand.
                event.accept()
            else:
                super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == QtCore.Qt.LeftButton and self._drag_position is not None:
                self._drag_position = None
                self.unsetCursor()
                event.accept()
            else:
                super().mouseReleaseEvent(event)

        def wheelEvent(self, event) -> None:
            if not self._viewport().contains(event.position()):
                event.ignore()
                return
            # angleDelta supports mouse wheels and most trackpads; pixelDelta
            # covers devices that only report smooth pixel scrolling.
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self._change_zoom(delta / 120.0)
                event.accept()
            else:
                event.ignore()

        def _viewport(self):
            """Leave room for status above the map and legends on its right."""
            return QtCore.QRectF(20, 85, self.width() - 280, self.height() - 105)

        def _screen_points(self, points: np.ndarray) -> np.ndarray:
            """Map projected scene coordinates into Qt pixel coordinates."""
            projected = project_points(points, self._view_axes)
            origin = self._viewport().center()
            return ((projected - self._camera_center) * self._camera_scale
                    * (1, -1) + (origin.x(), origin.y()))

        def _cache_scene(self) -> None:
            """Record ground, shadows, and faces for the current camera angles.

            Qt replays the picture with a pan/zoom transform at paint time, so
            following mobility does not rebuild the polygons or shadow geometry.
            """
            picture = QtGui.QPicture()
            painter = QtGui.QPainter(picture)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            # Free space has no ground geometry; z=0 is only a drawing reference.
            ground_z = float(scene.edges[..., 2].min()) if scene.edges.size else 0.0
            self._ground_z = ground_z
            ground_faces = []
            raised_faces = []
            for index, face in enumerate(scene.polygons):
                if np.allclose(face[:, 2], ground_z, rtol=0, atol=1e-4):
                    ground_faces.append(index)
                else:
                    raised_faces.append(index)
            raised_faces.sort(key=lambda i: float(np.mean(view_depth(scene.polygons[i], self._view_axes))))

            def polygon(points):
                return QtGui.QPolygonF([QtCore.QPointF(*point) for point in points])

            def draw_face(index):
                color = scene.polygon_colors[index]
                fill = QtGui.QColor(*material_shade(color, MATERIAL_FILL_BRIGHTNESS))
                edge = QtGui.QColor(*material_shade(color, MATERIAL_EDGE_BRIGHTNESS))
                points = polygon(project_points(scene.polygons[index], self._view_axes))
                pen = QtGui.QPen(edge, 1.2)
                pen.setCosmetic(True)  # Pixel-sized details remain readable at every zoom.
                painter.setPen(pen)
                painter.setBrush(QtGui.QBrush(fill))
                painter.drawPolygon(points)
                # Draw the face's mesh vertices in depth order too: nearer faces
                # can hide rear vertices, rather than exposing all mesh corners.
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(edge)
                for point in points:
                    painter.drawEllipse(point, VERTEX_RADIUS, VERTEX_RADIUS)

            ground_area = QtGui.QPainterPath()
            ground_area.setFillRule(QtCore.Qt.WindingFill)
            shadows = QtGui.QPainterPath()
            shadows.setFillRule(QtCore.Qt.WindingFill)

            def add_area(path, points):
                # Equal winding keeps overlapping polygons filled, and applying
                # opacity once avoids darker patches where mesh triangles overlap.
                following = np.roll(points, -1, axis=0)
                signed_area = np.sum(points[:, 0] * following[:, 1]
                                     - following[:, 0] * points[:, 1])
                if signed_area < 0:
                    points = points[::-1]
                path.addPolygon(polygon(points))
                path.closeSubpath()

            for index in ground_faces:
                draw_face(index)
                add_area(ground_area, project_points(scene.polygons[index], self._view_axes))
            for index in raised_faces:
                add_area(shadows, project_points(ground_shadow(scene.polygons[index], ground_z), self._view_axes))
            # Intersect once here, rather than recording clip commands that
            # would replace the viewport clip when Qt replays this picture.
            painter.fillPath(shadows.intersected(ground_area), QtGui.QColor(0, 0, 0, 75))
            painter.end()
            self._ground_picture = picture
            self._ground_area = ground_area

            # Separate caches let moving node shadows lie on the ground while
            # opaque building faces still cover them. Neither cache moves in RF space.
            picture = QtGui.QPicture()
            painter = QtGui.QPainter(picture)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            for index in raised_faces:
                draw_face(index)
            painter.end()
            self._scene_picture = picture

        def _draw_material_legend(self, painter) -> None:
            """Draw the material names and the fixed colors used in this scene."""
            row_height = 20
            width = 240
            height = 28 + row_height * len(scene.materials)
            left = max(12, self.width() - width - 12)
            top = 12
            painter.fillRect(left, top, width, height, QtGui.QColor(24, 28, 34, 220))
            painter.setPen(QtGui.QColor(225, 225, 225))
            painter.drawText(left + 8, top + 18, "Scene materials")
            for index, (name, color) in enumerate(scene.materials):
                y = top + 28 + index * row_height
                fill = QtGui.QColor(*material_shade(color, MATERIAL_FILL_BRIGHTNESS))
                painter.fillRect(left + 8, y, 12, 12, fill)
                painter.setPen(QtGui.QColor(225, 225, 225))
                painter.drawText(left + 28, y + 11, name)

        def _draw_overlay_legend(self, painter) -> None:
            """Label the node markers and Sionna propagation-path colors."""
            row_height = 20
            width = 240
            entries = len(NODE_COLORS) + len(PATH_COLORS)
            height = 28 + row_height * entries
            left = self.width() - width - 12
            top = 52 + 20 * len(scene.materials)
            painter.fillRect(left, top, width, height, QtGui.QColor(24, 28, 34, 220))
            painter.setPen(QtGui.QColor(225, 225, 225))
            painter.drawText(left + 8, top + 18, "Nodes and propagation paths")

            row = 0
            for name, color in NODE_COLORS.items():
                y = top + 28 + row * row_height
                qcolor = QtGui.QColor.fromRgbF(*color)
                painter.setPen(QtGui.QPen(qcolor, 2))
                painter.setBrush(QtGui.QBrush(qcolor))
                painter.drawEllipse(QtCore.QPointF(left + 14, y + 6), 5, 5)
                painter.setPen(QtGui.QColor(225, 225, 225))
                painter.drawText(left + 28, y + 11, name)
                row += 1

            for name, color in PATH_COLORS.items():
                y = top + 28 + row * row_height
                qcolor = QtGui.QColor.fromRgbF(*color)
                painter.setPen(QtGui.QPen(qcolor, 3))
                painter.drawLine(left + 8, y + 6, left + 20, y + 6)
                painter.setPen(QtGui.QColor(225, 225, 225))
                painter.drawText(left + 28, y + 11, name)
                row += 1

        def _refresh_state(self) -> None:
            """Repaint only after the producer atomically replaces the state."""
            # On macOS/Linux, an orphaned child is reparented. This also covers
            # forced termination, where the flowgraph cannot run stop hooks.
            if parent_pid is not None and os.getppid() != parent_pid:
                self.close()
                return
            try:
                modified = Path(state_path).stat().st_mtime_ns
                if modified == self._state_mtime_ns:
                    return
                self._state = load_state(state_path)
                self._state_mtime_ns = modified
            except (OSError, KeyError, ValueError):
                return
            self.update()

        def paintEvent(self, event) -> None:
            viewport = self._viewport()
            self._camera_center, self._camera_scale = fit_node_view(
                self._state.tx_position, self._state.rx_position,
                (viewport.width(), viewport.height()),
                self._view_axes,
            )
            self._camera_center += project_points(self._pan_offset, self._view_axes)
            self._camera_scale *= self._zoom_factor
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor(24, 28, 34))
            painter.save()
            painter.setClipRect(viewport)
            painter.translate(viewport.center())
            painter.scale(self._camera_scale, -self._camera_scale)
            painter.translate(-self._camera_center[0], -self._camera_center[1])
            painter.drawPicture(0, 0, self._ground_picture)
            self._draw_node_shadows(painter)
            painter.drawPicture(0, 0, self._scene_picture)
            painter.restore()
            painter.save()
            painter.setClipRect(viewport)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)

            if len(self._state.starts):
                starts = self._screen_points(self._state.starts)
                ends = self._screen_points(self._state.ends)
                for start, end, color in zip(starts, ends, self._state.colors):
                    red, green, blue = np.clip(color, 0.0, 1.0)
                    painter.setPen(
                        QtGui.QPen(
                            QtGui.QColor.fromRgbF(float(red), float(green), float(blue)),
                            2,
                        )
                    )
                    painter.drawLine(
                        QtCore.QLineF(start[0], start[1], end[0], end[1])
                    )

            self._draw_node(
                painter,
                self._state.tx_position,
                "TX",
                QtGui.QColor.fromRgbF(*NODE_COLORS["TX node"]),
            )
            self._draw_node(
                painter,
                self._state.rx_position,
                "RX",
                QtGui.QColor.fromRgbF(*NODE_COLORS["RX node"]),
            )
            painter.restore()
            painter.setPen(QtGui.QColor(225, 225, 225))
            painter.drawText(12, 22, f"Propagation segments: {len(self._state.starts)}")
            tx = ", ".join(f"{value:.2f}" for value in self._state.tx_position)
            rx = ", ".join(f"{value:.2f}" for value in self._state.rx_position)
            painter.drawText(12, 42, f"TX [m]: ({tx})")
            painter.drawText(12, 62, f"RX [m]: ({rx})")
            self._draw_material_legend(painter)
            self._draw_overlay_legend(painter)
            painter.end()

        def _draw_node_shadows(self, painter) -> None:
            """Draw TX/RX marker shadows in projected scene coordinates.

            Use the building-shadow light direction and ground plane. Ellipse
            radii stay 8x4 screen pixels: a visual marker, not an antenna size.
            The ground clip and later building layer keep shadows on the map.
            """
            painter.save()
            painter.setClipPath(self._ground_area, QtCore.Qt.IntersectClip)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 110))
            for position in (self._state.tx_position, self._state.rx_position):
                if position[2] < self._ground_z:
                    continue  # A node below the assumed plane casts no ground shadow.
                point = project_points(ground_shadow(position, self._ground_z), self._view_axes)
                painter.drawEllipse(QtCore.QPointF(*point),
                                    8 / self._camera_scale, 4 / self._camera_scale)
            painter.restore()

        def _draw_node(self, painter, position, label, color) -> None:
            point = self._screen_points(np.asarray(position).reshape(1, 3))[0]
            painter.setPen(QtGui.QPen(color, 2))
            painter.setBrush(QtGui.QBrush(color))
            painter.drawEllipse(QtCore.QPointF(point[0], point[1]), 6, 6)
            painter.drawText(int(point[0] + 9), int(point[1] - 7), label)

    app = QtWidgets.QApplication(sys.argv)
    view = SceneView()
    view.show()
    return app.exec_()


def main() -> None:
    """Parse the static scene and live-state inputs, then run the Qt viewer."""
    args = _parser().parse_args()
    raise SystemExit(_run_qt(args.scene, args.state, args.parent_pid))


if __name__ == "__main__":
    main()
