"""Render edited image-space traces without claiming measured 3D flight."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

import cv2
import numpy as np

from .models import Project, TrackPoint


class ExportCancelled(Exception):
    """An export was cancelled; no completed destination was replaced."""


def _matrix(transforms, frame_index: int) -> np.ndarray:
    if not transforms:
        return np.eye(3, dtype=np.float64)
    value = transforms.get(frame_index)
    if value is None:
        # A manually edited frame without a measured transform uses the nearest
        # available camera position. This is an editing fallback, not a measurement.
        key = min(transforms, key=lambda index: abs(index - frame_index))
        value = transforms[key]
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        return np.eye(3, dtype=np.float64)
    return matrix


def _reference_points(points, transforms):
    unique = {point.frame: point for point in points}
    frames = sorted(unique)
    coordinates = []
    for index in frames:
        point = unique[index]
        position = np.array([point.x, point.y, 1.0], dtype=np.float64)
        try:
            position = np.linalg.solve(_matrix(transforms, index), position)
        except np.linalg.LinAlgError:
            pass
        if abs(position[2]) < 1e-9 or not np.isfinite(position).all():
            continue
        coordinates.append((index, position[:2] / position[2]))
    return coordinates


def draw_trace(frame_bgr, frame_index, points, transforms=None, color="#42e0ad", line_width=5):
    """Draw the known path up to this frame; interpolate only between anchors.

    Coordinates are 2D image positions. Manual anchors and tracked observations
    are retained separately in the project; this display interpolates between
    them and never extends a trajectory past its final anchor.
    """
    output = frame_bgr.copy()
    if not points:
        return output
    anchors = _reference_points(points, transforms)
    if not anchors or frame_index < anchors[0][0]:
        return output
    visible = []
    for offset, (index, position) in enumerate(anchors):
        if index <= frame_index:
            visible.append(position)
            continue
        if offset > 0:
            previous_index, previous_position = anchors[offset - 1]
            fraction = (frame_index - previous_index) / (index - previous_index)
            visible.append(previous_position * (1 - fraction) + position * fraction)
        break
    if not visible:
        return output
    positions = np.column_stack([np.asarray(visible), np.ones(len(visible))])
    transformed = (_matrix(transforms, frame_index) @ positions.T).T
    valid = np.abs(transformed[:, 2]) > 1e-9
    transformed = transformed[valid]
    if not len(transformed):
        return output
    pixels = transformed[:, :2] / transformed[:, 2, None]
    # Bound extreme project-file coordinates before conversion to OpenCV ints.
    pixels = np.rint(np.clip(pixels, -1_000_000, 1_000_000)).astype(np.int32)
    bgr = tuple(int(color[index:index + 2], 16) for index in (5, 3, 1))
    width = min(24, max(1, int(line_width)))
    if len(pixels) >= 2:
        glow = output.copy()
        cv2.polylines(glow, [pixels.reshape(-1, 1, 2)], False, bgr, width + 8, cv2.LINE_AA)
        output = cv2.addWeighted(glow, 0.17, output, 0.83, 0)
        cv2.polylines(output, [pixels.reshape(-1, 1, 2)], False, bgr, width, cv2.LINE_AA)
        if width >= 4:
            highlight = tuple(min(255, int(channel * 0.4 + 153)) for channel in bgr)
            cv2.polylines(output, [pixels.reshape(-1, 1, 2)], False, highlight, 1, cv2.LINE_AA)
    cv2.circle(output, tuple(pixels[-1]), max(3, width), bgr, -1, cv2.LINE_AA)
    cv2.circle(output, tuple(pixels[-1]), max(1, width // 2), (245, 255, 250), -1, cv2.LINE_AA)
    return output


def _encode_video(video_path, output_path, project: Project, progress=None, cancelled=None, *, normalize=False):
    """Atomically export a silent MP4 using a local OpenCV encoder.

    The source is never modified. Audio is deliberately not copied in v0.1;
    both the interface and returned result expose that limitation.
    """
    source = Path(video_path).resolve()
    destination = Path(output_path).resolve()
    if os.path.normcase(str(source)) == os.path.normcase(str(destination)):
        raise ValueError("导出路径不能覆盖原视频，请使用新的文件名")
    if destination.suffix.lower() != ".mp4":
        raise ValueError("导出文件须使用 .mp4 扩展名")
    if not normalize and len(project.points) < 2:
        raise ValueError("请先添加至少两个轨迹点再导出")
    if not destination.parent.is_dir():
        raise ValueError("导出目录不存在")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise ValueError("无法读取源视频")
    temporary = destination.with_name(f".golftrace-{uuid.uuid4().hex}.mp4")
    writer = None
    complete = False
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if width <= 0 or height <= 0 or not np.isfinite(fps) or fps <= 0:
            raise ValueError("视频分辨率或帧率无效")
        writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError("无法创建 MP4 编码器；请检查目录权限及视频格式")
        count = 0
        if progress:
            progress(0, total)
        while True:
            if cancelled and cancelled():
                raise ExportCancelled("导出已取消")
            ok, frame = capture.read()
            if not ok:
                break
            result = draw_trace(frame, count, project.points, project.transforms, project.color, project.line_width)
            writer.write(result)
            count += 1
            if progress:
                progress(count, total)
        writer.release()
        writer = None
        capture.release()
        if count == 0 or (not normalize and total > 0 and count < total - 1):
            raise RuntimeError(f"源视频读取不完整：仅解码 {count} / {total} 帧，未生成成品")
        if cancelled and cancelled():
            raise ExportCancelled("导出已取消")
        verification = cv2.VideoCapture(str(temporary))
        try:
            readable, _ = verification.read()
            written = int(verification.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:
            verification.release()
        if not readable or written != count:
            raise RuntimeError("导出文件未通过解码校验，未替换目标文件")
        temporary.replace(destination)
        complete = True
        return {"path": str(destination), "frame_count": count, "fps": fps, "width": width,
                "height": height, "audio_preserved": False, "normalized": normalize,
                "input_metadata_frames": total, "duration": count / fps}
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if not complete and temporary.exists():
            temporary.unlink()


def export_video(video_path, output_path, project: Project, progress=None, cancelled=None):
    """Render a trace to a new silent MP4, verifying complete frame decoding."""
    return _encode_video(video_path, output_path, project, progress, cancelled)


def normalize_video(video_path, output_path, progress=None, cancelled=None):
    """Create a CFR editing copy from the actual sequential decode sequence.

    This explicitly rebases timing to the nominal FPS and omits audio. It is a
    user-selected recovery path for files whose PTS and nominal frame counts
    disagree, not a claim to preserve the source's variable frame timestamps.
    """
    return _encode_video(video_path, output_path, Project(str(video_path)), progress, cancelled, normalize=True)
