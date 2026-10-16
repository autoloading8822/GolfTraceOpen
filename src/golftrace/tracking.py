"""Visible white-ball tracking, with measured background camera compensation.

This is a classical image tracker, not a trained detector or a measurement of an
invisible ball. Motion prediction only gates the next image search; it never
creates an output point when there is no matching visible image component.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .models import TrackPoint, TrackResult, VideoInfo


def _open_video(path: str | Path) -> tuple[cv2.VideoCapture, Path]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"找不到视频文件：{source}")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        raise ValueError(f"无法打开视频，文件可能已损坏或编码不受支持：{source}")
    return capture, source


def _video_info(capture: cv2.VideoCapture, source: Path) -> VideoInfo:
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    ok, frame = capture.read()
    if not ok or frame is None or frame.size == 0:
        raise ValueError(f"无法解码视频首帧，请转换为 H.264 MP4 或 MJPEG AVI：{source}")
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"视频没有有效帧率，暂不支持该文件：{source}")
    if not math.isfinite(count) or count < 1:
        raise ValueError(f"视频没有有效帧数，暂不支持该文件：{source}")
    height, width = frame.shape[:2]
    return VideoInfo(str(source), width, height, fps, int(round(count)))


def inspect_video(path: str | Path) -> VideoInfo:
    """Decode the first frame and return validated metadata (Unicode paths work)."""
    capture, source = _open_video(path)
    try:
        return _video_info(capture, source)
    finally:
        capture.release()


def _seek_frame(capture: cv2.VideoCapture, index: int, source: Path) -> np.ndarray:
    if not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
        raise ValueError(f"该视频不支持逐帧定位，请先转换为 H.264 MP4：{source}")
    ok, frame = capture.read()
    if not ok or frame is None:
        raise ValueError(f"无法解码第 {index + 1} 帧，视频可能不完整或编码不受支持：{source}")
    return frame


def read_frame(path: str | Path, index: int) -> np.ndarray:
    """Read a zero-based frame as an OpenCV BGR array."""
    if not isinstance(index, (int, np.integer)) or isinstance(index, bool):
        raise ValueError("帧编号必须为整数")
    capture, source = _open_video(path)
    try:
        info = _video_info(capture, source)
        if not 0 <= index < info.frame_count:
            raise ValueError(f"帧编号超出范围：0 至 {info.frame_count - 1}")
        return _seek_frame(capture, int(index), source)
    finally:
        capture.release()


def _camera_step(previous: np.ndarray, current: np.ndarray,
                 ball_xy: np.ndarray, ball_radius: float) -> np.ndarray:
    """Fit previous -> current using background LK tracks and RANSAC.

    A similarity affine transform handles translation, rotation and uniform zoom.
    If background evidence is insufficient, identity is the explicit fallback.
    """
    height, width = previous.shape
    scale = min(1.0, 960.0 / max(height, width))
    if scale < 1.0:
        size = (round(width * scale), round(height * scale))
        old = cv2.resize(previous, size, interpolation=cv2.INTER_AREA)
        new = cv2.resize(current, size, interpolation=cv2.INTER_AREA)
    else:
        old, new = previous, current
    mask = np.full(old.shape, 255, dtype=np.uint8)
    center = tuple(np.rint(ball_xy * scale).astype(int))
    cv2.circle(mask, center, max(12, round(ball_radius * 4 * scale)), 0, -1)
    old_points = cv2.goodFeaturesToTrack(
        old, maxCorners=240, qualityLevel=0.01, minDistance=9,
        mask=mask, blockSize=7,
    )
    identity = np.eye(3, dtype=np.float64)
    if old_points is None or len(old_points) < 10:
        return identity
    new_points, forward_status, _ = cv2.calcOpticalFlowPyrLK(
        old, new, old_points, None, winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 25, 0.02),
    )
    if new_points is None or forward_status is None:
        return identity
    back_points, backward_status, _ = cv2.calcOpticalFlowPyrLK(
        new, old, new_points, None, winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 25, 0.02),
    )
    if back_points is None or backward_status is None:
        return identity
    error = np.linalg.norm(back_points.reshape(-1, 2) - old_points.reshape(-1, 2), axis=1)
    valid = (forward_status.ravel() == 1) & (backward_status.ravel() == 1) & (error < 1.6)
    if np.count_nonzero(valid) < 10:
        return identity
    affine, inliers = cv2.estimateAffinePartial2D(
        old_points.reshape(-1, 2)[valid], new_points.reshape(-1, 2)[valid],
        method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=1500, confidence=0.99,
    )
    if affine is None or inliers is None or np.count_nonzero(inliers) < 8:
        return identity
    if np.mean(inliers) < 0.4 or not np.all(np.isfinite(affine)):
        return identity
    zoom = math.hypot(float(affine[0, 0]), float(affine[1, 0]))
    if not 0.90 <= zoom <= 1.10:
        return identity
    affine[:, 2] /= scale
    if np.linalg.norm(affine[:, 2]) > max(height, width) * 0.25:
        return identity
    identity[:2] = affine
    return identity


def _map_point(matrix: np.ndarray, xy: np.ndarray) -> np.ndarray:
    return (matrix @ np.array([xy[0], xy[1], 1.0]))[:2]


def _find_ball(frame: np.ndarray, predicted: np.ndarray, search_radius: float,
               radius: float, reference_area: float,
               residual: np.ndarray | None = None,
               stationary_xy: np.ndarray | None = None,
               allow_stationary: bool = False) -> tuple[np.ndarray, float, float] | None:
    """Score small bright neutral components using appearance and measured motion."""
    height, width = frame.shape[:2]
    x0 = max(0, math.floor(float(predicted[0]) - search_radius))
    y0 = max(0, math.floor(float(predicted[1]) - search_radius))
    x1 = min(width, math.ceil(float(predicted[0]) + search_radius + 1))
    y1 = min(height, math.ceil(float(predicted[1]) + search_radius + 1))
    if x0 >= x1 or y0 >= y1:
        return None
    crop = frame[y0:y1, x0:x1]
    channels = crop.astype(np.int16)
    brightest = channels.max(axis=2)
    chroma = brightest - channels.min(axis=2)
    mask = ((brightest >= 160) & (chroma <= 80)).astype(np.uint8)
    number, labels, stats, centers = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best = None
    best_score = 0.0
    max_area = max(30.0, math.pi * (radius * 2.5) ** 2)
    for label in range(1, number):
        left, top, component_width, component_height, area = stats[label]
        if area < 2 or area > max_area:
            continue
        if max(component_width, component_height) > max(14.0, radius * 6):
            continue
        if min(component_width, component_height) / max(component_width, component_height) < 0.18:
            continue
        xy = centers[label] + np.array([x0, y0], dtype=np.float64)
        distance = float(np.linalg.norm(xy - predicted))
        if distance > search_radius:
            continue
        pixels = labels[top:top + component_height, left:left + component_width] == label
        brightness = float(brightest[top:top + component_height, left:left + component_width][pixels].mean())
        appearance_score = min(1.0, max(0.0, (brightness - 140.0) / 100.0))
        distance_score = math.exp(-0.5 * (distance / max(2.0, search_radius * 0.55)) ** 2)
        size_score = math.exp(-0.45 * abs(math.log(max(float(area), 1.0) / max(reference_area, 1.0))))
        motion_score = 1.0
        if residual is not None:
            changes = residual[y0 + top:y0 + top + component_height,
                               x0 + left:x0 + left + component_width][pixels]
            energy = float(changes.mean())
            is_stationary = (
                allow_stationary and stationary_xy is not None
                and np.linalg.norm(xy - stationary_xy) < max(2.5, radius * 0.6)
            )
            # Subpixel registration and codec ringing can change static highlight
            # edges. A moving candidate needs substantive new bright pixels.
            if not is_stationary and (energy < 15.0 or np.mean(changes > 20) < 0.20):
                continue
            motion_score = max(0.35 if is_stationary else 0.0, min(1.0, energy / 55.0))
        score = 0.28 * appearance_score + 0.30 * motion_score + 0.27 * distance_score + 0.15 * size_score
        if score > best_score and score >= 0.55:
            best_score = score
            best = (xy, min(0.99, score), float(area))
    return best


def track_video(video_path: str | Path, start_frame: int,
                initial_xy: tuple[float, float], ball_radius: float = 5.0,
                end_frame: int | None = None,
                progress: Callable[[int, int], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> TrackResult:
    """Track visible white ball components, ending detection after three misses.

    Frame numbers and coordinates refer to the original decoded video. end_frame
    is inclusive. progress receives processed/requested frame counts. last_frame
    is the last inspected frame, including missing detections; no missing point
    is interpolated. After loss, camera-only analysis continues to final_frame.
    transforms maps the start frame into each inspected frame.
    """
    capture, source = _open_video(video_path)
    try:
        info = _video_info(capture, source)
        if not isinstance(start_frame, (int, np.integer)) or isinstance(start_frame, bool):
            raise ValueError("起始帧编号必须为整数")
        if not 0 <= start_frame < info.frame_count:
            raise ValueError(f"起始帧超出范围：0 至 {info.frame_count - 1}")
        if end_frame is not None and (not isinstance(end_frame, (int, np.integer)) or isinstance(end_frame, bool)):
            raise ValueError("结束帧编号必须为整数")
        final_frame = info.frame_count - 1 if end_frame is None else int(end_frame)
        if not start_frame <= final_frame < info.frame_count:
            raise ValueError("结束帧必须位于起始帧与视频末帧之间")
        if not math.isfinite(ball_radius) or not 1 <= ball_radius <= max(info.width, info.height) / 4:
            raise ValueError("球半径必须是大于等于 1 的有效像素值")
        selected = np.asarray(initial_xy, dtype=np.float64)
        if selected.shape != (2,) or not np.all(np.isfinite(selected)):
            raise ValueError("起球位置必须包含两个有效坐标")
        if not (0 <= selected[0] < info.width and 0 <= selected[1] < info.height):
            raise ValueError("起球位置必须位于视频图像内")
        points: list[TrackPoint] = []
        transforms: dict[int, list[list[float]]] = {}
        last_frame = int(start_frame) - 1
        if cancelled is not None and cancelled():
            return TrackResult(info, points, "cancelled", last_frame, transforms)
        frame = _seek_frame(capture, int(start_frame), source)
        cumulative = np.eye(3, dtype=np.float64)
        transforms[int(start_frame)] = cumulative.tolist()
        last_frame = int(start_frame)
        found = _find_ball(frame, selected, max(7.0, ball_radius * 2.5),
                           ball_radius, math.pi * ball_radius ** 2)
        total = final_frame - int(start_frame) + 1
        if progress is not None:
            progress(1, total)
        if found is None:
            return TrackResult(info, points, "lost", last_frame, transforms)
        xy, confidence, reference_area = found
        points.append(TrackPoint(int(start_frame), float(xy[0]), float(xy[1]), confidence, "tracked"))
        previous_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        last_stable = xy.copy()
        last_detection_frame = int(start_frame)
        velocity = np.zeros(2, dtype=np.float64)
        misses = 0
        reason = "end_frame" if end_frame is not None else "end_of_video"
        for index in range(int(start_frame) + 1, final_frame + 1):
            if cancelled is not None and cancelled():
                reason = "cancelled"
                break
            ok, frame = capture.read()
            if not ok or frame is None:
                raise ValueError(f"无法解码第 {index + 1} 帧，视频可能不完整或编码不受支持：{source}")
            current_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            old_ball_xy = _map_point(cumulative, last_stable)
            step = _camera_step(previous_gray, current_gray, old_ball_xy, ball_radius)
            cumulative = step @ cumulative
            transforms[index] = cumulative.tolist()
            last_frame = index
            if reason == "lost":
                previous_gray = current_gray
                if progress is not None:
                    progress(index - int(start_frame) + 1, total)
                continue
            gap = index - last_detection_frame
            predicted = _map_point(cumulative, last_stable + velocity * gap)
            stationary = _map_point(cumulative, last_stable)
            warped = cv2.warpAffine(previous_gray, step[:2], (info.width, info.height))
            residual = np.maximum(current_gray.astype(np.int16) - warped.astype(np.int16), 0)
            if len(points) == 1:
                search_radius = max(28.0, ball_radius * 13, min(info.width, info.height) * 0.18)
            else:
                search_radius = max(ball_radius * 5, 10.0, float(np.linalg.norm(velocity)) * 0.75 + 8.0)
                search_radius *= 1.0 + misses * 0.5
            found = _find_ball(
                frame, predicted, search_radius, ball_radius, reference_area, residual,
                stationary, allow_stationary=float(np.linalg.norm(velocity)) < 1.5,
            )
            if found is None:
                misses += 1
            else:
                xy, confidence, area = found
                stable = _map_point(np.linalg.inv(cumulative), xy)
                measured_velocity = (stable - last_stable) / gap
                velocity = measured_velocity if len(points) == 1 else 0.7 * measured_velocity + 0.3 * velocity
                last_stable = stable
                last_detection_frame = index
                reference_area = 0.9 * reference_area + 0.1 * area
                points.append(TrackPoint(index, float(xy[0]), float(xy[1]), confidence, "tracked"))
                misses = 0
            previous_gray = current_gray
            if progress is not None:
                progress(index - int(start_frame) + 1, total)
            if misses >= 3:
                reason = "lost"
        return TrackResult(info, points, reason, last_frame, transforms)
    finally:
        capture.release()
