"""Synthetic export checks; these do not establish real-course tracking accuracy."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from golftrace.models import Project, TrackPoint
from golftrace.render import ExportCancelled, draw_trace, export_video, normalize_video


def make_video(path: Path, frames=15):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (160, 120))
    assert writer.isOpened()
    for index in range(frames):
        image = np.full((120, 160, 3), (40, 80, 30), dtype=np.uint8)
        cv2.circle(image, (20 + index * 4, 80 - index * 2), 3, (255, 255, 255), -1)
        writer.write(image)
    writer.release()


def test_camera_reprojection_and_no_future_trace():
    image = np.zeros((120, 160, 3), np.uint8)
    points = [TrackPoint(0, 20, 80), TrackPoint(10, 90, 40)]
    matrices = {0: np.eye(3).tolist(), 10: np.array([[1, 0, 30], [0, 1, 0], [0, 0, 1]]).tolist()}
    assert not draw_trace(image, -1, points, matrices).any()
    result = draw_trace(image, 10, points, matrices)
    assert result[80, 50].any()  # First point moves with the measured camera.
    assert result[40, 90].any()
    assert not result[80, 20].any()


def test_silent_export_is_readable_and_preserves_source(tmp_path):
    source = tmp_path / "原始高尔夫.avi"
    make_video(source)
    before = source.read_bytes()
    destination = tmp_path / "轨迹成片.mp4"
    project = Project(str(source), [TrackPoint(0, 20, 80), TrackPoint(14, 76, 52)])
    result = export_video(str(source), str(destination), project)
    assert result["audio_preserved"] is False
    assert result["frame_count"] == 15
    assert source.read_bytes() == before
    output = cv2.VideoCapture(str(destination))
    assert output.isOpened()
    output.set(cv2.CAP_PROP_POS_FRAMES, 14)
    ok, final = output.read()
    output.release()
    assert ok and final.shape == (120, 160, 3)
    assert final[65, 50, 1] > 100


def test_cancel_does_not_replace_existing_output(tmp_path):
    source = tmp_path / "source.avi"
    make_video(source)
    destination = tmp_path / "existing.mp4"
    destination.write_bytes(b"previous-user-output")
    project = Project(str(source), [TrackPoint(0, 20, 80), TrackPoint(14, 76, 52)])
    with pytest.raises(ExportCancelled):
        export_video(str(source), str(destination), project, cancelled=lambda: True)
    assert destination.read_bytes() == b"previous-user-output"
    assert not list(tmp_path.glob(".golftrace-*"))


def test_source_overwrite_rejected(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unchanged")
    project = Project(str(source), [TrackPoint(0, 20, 80), TrackPoint(1, 24, 78)])
    with pytest.raises(ValueError, match="原视频"):
        export_video(str(source), str(source), project)
    assert source.read_bytes() == b"unchanged"


def test_project_round_trip_and_invalid_number_rejection(tmp_path):
    project = Project("原视频.mp4", [TrackPoint(0, 12, 34, 0.9, "tracked"), TrackPoint(5, 22, 18)])
    path = tmp_path / "编辑.golftrace.json"
    project.save(str(path))
    restored = Project.load(str(path))
    assert restored == project
    path.write_text('{"schema_version":1,"points":[{"frame":0,"x":NaN,"y":3}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="无效"):
        Project.load(str(path))


def test_compatibility_copy_does_not_require_trace_or_change_source(tmp_path):
    source = tmp_path / "原片.avi"
    make_video(source)
    before = source.read_bytes()
    result = normalize_video(str(source), str(tmp_path / "兼容副本.mp4"))
    assert result["normalized"] is True
    assert result["audio_preserved"] is False
    assert result["frame_count"] == 15
    assert result["duration"] == 0.5
    assert source.read_bytes() == before
