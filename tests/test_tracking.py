"""Synthetic-video evidence only: these tests do not validate real golf footage."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from golftrace.tracking import inspect_video, read_frame, track_video


def _synthetic_video(path: Path, *, count: int = 32,
                     camera_motion: tuple[float, float] = (0.0, 0.0),
                     disappears_at: int | None = None,
                     starts_moving_at: int = 0,
                     hidden_frames: tuple[int, ...] = ()) -> np.ndarray:
    """Write known bright-ball positions over independently moving texture."""
    width, height = 384, 256
    rng = np.random.default_rng(781)
    background = rng.integers(20, 105, (height, width, 3), dtype=np.uint8)
    background = cv2.GaussianBlur(background, (3, 3), 0)
    for _ in range(75):
        x, y = rng.integers([8, 8], [width - 8, height - 8])
        color = tuple(int(value) for value in rng.integers(35, 135, 3))
        cv2.rectangle(background, (int(x), int(y)), (int(x + 5), int(y + 5)), color, -1)
    # A static white distractor near a later ball position must not extend a lost track.
    cv2.circle(background, (136, 170), 4, (245, 245, 245), -1)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30.0, (width, height))
    assert writer.isOpened(), "Synthetic evidence requires the MJPEG encoder"
    expected = []
    try:
        for index in range(count):
            dx, dy = np.array(camera_motion) * index
            affine = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float64)
            frame = cv2.warpAffine(background, affine, (width, height))
            motion_index = max(0, index - starts_moving_at)
            stable = np.array([60.0 + 5.0 * motion_index,
                               190.0 - 2.5 * motion_index + 0.025 * motion_index ** 2])
            xy = stable + [dx, dy]
            expected.append(xy)
            if (disappears_at is None or index < disappears_at) and index not in hidden_frames:
                cv2.circle(frame, tuple(np.rint(xy).astype(int)), 4, (250, 250, 250), -1, cv2.LINE_AA)
            writer.write(frame)
    finally:
        writer.release()
    return np.array(expected)


def _errors(result, expected):
    return np.array([np.linalg.norm([point.x - expected[point.frame, 0],
                                    point.y - expected[point.frame, 1]]) for point in result.points])


def test_synthetic_motion_positions_and_unicode_path(tmp_path):
    path = tmp_path / "高尔夫 合成证据.avi"
    expected = _synthetic_video(path)
    info = inspect_video(path)
    assert (info.width, info.height, info.frame_count, info.fps) == (384, 256, 32, 30.0)
    frame = read_frame(path, 9)
    assert frame.shape == (256, 384, 3)
    result = track_video(path, 0, tuple(expected[0]), ball_radius=4)
    assert result.stop_reason == "end_of_video"
    assert len(result.points) >= 30
    assert np.quantile(_errors(result, expected), 0.95) < 2.0
    assert all(point.source == "tracked" and 0 < point.confidence <= 1 for point in result.points)


@pytest.mark.parametrize("start_frame", [0, 7])
def test_synthetic_camera_translation_is_measured(tmp_path, start_frame):
    path = tmp_path / "camera-motion.avi"
    motion = (1.4, 0.7)
    expected = _synthetic_video(path, camera_motion=motion)
    result = track_video(path, start_frame, tuple(expected[start_frame]), ball_radius=4)
    assert result.stop_reason == "end_of_video"
    assert len(result.points) >= 30 - start_frame
    assert np.quantile(_errors(result, expected), 0.95) < 2.2
    matrix = np.array(result.transforms[31])
    assert np.linalg.norm(matrix[:2, 2] - np.array(motion) * (31 - start_frame)) < 2.0
    assert np.allclose(matrix[2], [0, 0, 1])


def test_synthetic_ball_loss_stops_without_inventing_points(tmp_path):
    path = tmp_path / "lost.avi"
    expected = _synthetic_video(path, camera_motion=(1.1, 0.4), disappears_at=14)
    result = track_video(path, 0, tuple(expected[0]), ball_radius=4)
    assert result.stop_reason == "lost"
    assert result.points[-1].frame == 13
    assert result.last_frame == 31
    assert max(result.transforms) == 31
    assert np.linalg.norm(np.array(result.transforms[31])[:2, 2] - np.array([1.1, 0.4]) * 31) < 2.0
    assert np.max(_errors(result, expected)) < 2.0


def test_synthetic_short_gap_does_not_create_interpolated_points(tmp_path):
    path = tmp_path / "brief-gap.avi"
    expected = _synthetic_video(path, hidden_frames=(12, 13))
    result = track_video(path, 0, tuple(expected[0]), ball_radius=4)
    assert result.stop_reason == "end_of_video"
    frames = {point.frame for point in result.points}
    assert 12 not in frames and 13 not in frames and 14 in frames
    assert np.max(_errors(result, expected)) < 2.0


def test_synthetic_stationary_ball_can_launch_after_camera_pan(tmp_path):
    path = tmp_path / "stationary-then-launch.avi"
    expected = _synthetic_video(path, camera_motion=(1.2, 0.6), starts_moving_at=7)
    result = track_video(path, 0, tuple(expected[0]), ball_radius=4)
    assert result.stop_reason == "end_of_video"
    assert len(result.points) == 32
    assert np.max(_errors(result, expected)) < 2.0


def test_end_frame_and_cancellation_are_explicit(tmp_path):
    path = tmp_path / "bounds.avi"
    expected = _synthetic_video(path)
    progress = []
    bounded = track_video(path, 5, tuple(expected[5]), end_frame=10,
                          progress=lambda current, total: progress.append((current, total)))
    assert bounded.stop_reason == "end_frame"
    assert [point.frame for point in bounded.points] == list(range(5, 11))
    assert progress[-1] == (6, 6)
    assert np.allclose(bounded.transforms[5], np.eye(3))
    progress.clear()
    stopped = track_video(path, 0, tuple(expected[0]),
                          progress=lambda current, total: progress.append((current, total)),
                          cancelled=lambda: bool(progress and progress[-1][0] >= 5))
    assert stopped.stop_reason == "cancelled"
    assert stopped.last_frame == 4
    assert stopped.points[-1].frame == 4


def test_invalid_video_and_frame_inputs_fail_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="找不到视频"):
        inspect_video(tmp_path / "missing.mp4")
    invalid = tmp_path / "unsupported.mp4"
    invalid.write_bytes(b"This is synthetic invalid video data.")
    with pytest.raises(ValueError, match="无法打开视频|无法解码"):
        inspect_video(invalid)
    valid = tmp_path / "valid.avi"
    expected = _synthetic_video(valid, count=4)
    with pytest.raises(ValueError, match="超出范围"):
        read_frame(valid, 4)
    with pytest.raises(ValueError, match="位置必须"):
        track_video(valid, 0, (float("nan"), expected[0, 1]))
    no_ball = track_video(valid, 0, (300.0, 30.0))
    assert no_ball.stop_reason == "lost" and no_ball.points == []
