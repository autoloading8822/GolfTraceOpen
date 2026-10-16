"""Qt-widget integration tests using actual local video processing, offscreen.

Synthetic motion is controlled test evidence, not an accuracy claim for golf footage.
Dialogs select local test destinations; tracker, renderer, threads and video I/O run.
"""

import os
from pathlib import Path
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

import golftrace.gui as gui
from golftrace.models import Project
from test_tracking import _synthetic_video


@pytest.fixture(scope="session")
def qt_app():
    app = gui.create_application([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def editor(qt_app, monkeypatch):
    window = gui.MainWindow()
    errors = []
    window._error = lambda title, details: errors.append((title, details))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)

    def accept_dialog(parent, title, text, buttons, default):
        if buttons & QMessageBox.StandardButton.Save:
            return QMessageBox.StandardButton.Discard
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", accept_dialog)
    window.show()
    qt_app.processEvents()
    yield window, errors
    if window._thread:
        window._cancel_job()
        wait_for(qt_app, lambda: window._thread is None)
    window._dirty = False
    window.close()
    qt_app.processEvents()


def wait_for(app, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError("Qt operation did not finish before timeout")


def load_synthetic(window, tmp_path, **kwargs):
    path = tmp_path / "合成视频_GUI证据.avi"
    expected = _synthetic_video(path, **kwargs)
    assert window._load_video(str(path), Project(video_path=str(path)))
    return path, expected


def click_video(window, x, y):
    rect = window.canvas.image_rect()
    position = QPoint(round(rect.left() + x * rect.width() / window.info.width),
                      round(rect.top() + y * rect.height() / window.info.height))
    QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=position)


def assert_restored(window):
    assert window._thread is None
    assert window.import_button.isEnabled()
    assert window.open_project_button.isEnabled()
    assert window.timeline.isEnabled()
    assert window.canvas.isEnabled()
    assert not window.cancel_button.isVisible()


def test_point_mapping_frame_controls_manual_edits_and_project(editor, qt_app, tmp_path, monkeypatch):
    window, errors = editor
    assert not window.track_button.isEnabled()
    assert not window.export_button.isEnabled()
    path, expected = load_synthetic(window, tmp_path)
    window.resize(1000, 680)
    qt_app.processEvents()
    assert window.canvas.map_to_video(QPointF(-1, -1)) is None
    click_video(window, *expected[0])
    assert len(window.project.points) == 1
    assert np.linalg.norm(np.array([window._seed.x, window._seed.y]) - expected[0]) < 1
    assert window.track_button.isEnabled()
    assert not window.export_button.isEnabled()
    QTest.mouseClick(window.next_button, Qt.MouseButton.LeftButton)
    assert window.current_frame == 1
    QTest.mouseClick(window.previous_button, Qt.MouseButton.LeftButton)
    assert window.current_frame == 0
    window.frame_spin.setValue(8)
    assert window.current_frame == 7
    click_video(window, *expected[7])
    assert len(window.project.points) == 2
    assert window.export_button.isEnabled()
    click_video(window, expected[7, 0] + 2, expected[7, 1] + 1)
    assert len(window.project.points) == 2
    assert window.project.points[0].frame == 0
    assert window._seed.frame == 7
    QTest.mouseClick(window.remove_point_button, Qt.MouseButton.LeftButton)
    assert len(window.project.points) == 1
    assert not window.export_button.isEnabled()
    assert not window.track_button.isEnabled()
    click_video(window, *expected[7])
    project_path = tmp_path / "我的轨迹.golftrace.json"
    window.project_path = str(project_path)
    assert window._save_project()
    saved = Project.load(str(project_path))
    assert len(saved.points) == 2
    assert saved.video_path == str(path.resolve())
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *args: (str(project_path), ""))
    QTest.mouseClick(window.open_project_button, Qt.MouseButton.LeftButton)
    assert len(window.project.points) == 2
    assert window.project_path == str(project_path)
    assert not window._dirty
    window._toggle_playback()
    wait_for(qt_app, lambda: window.current_frame > 0)
    window._stop_playback()
    assert not window._play_timer.isActive()
    assert window.time_label.geometry().right() < window.time_label.parentWidget().width()
    output = Path(__file__).resolve().parents[1] / "artifacts" / "ui-minimum"
    output.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(output / "screenshot.png"))
    assert not errors


def test_real_worker_tracking_completes_and_preserves_manual_edits(editor, qt_app, tmp_path):
    window, errors = editor
    _, expected = load_synthetic(window, tmp_path)
    qt_app.processEvents()
    window.radius_spin.setValue(4)
    click_video(window, *expected[0])
    QTest.mouseClick(window.track_button, Qt.MouseButton.LeftButton)
    assert window._thread is not None
    assert not window.import_button.isEnabled()
    assert not window.canvas.isEnabled()
    wait_for(qt_app, lambda: window._thread is None)
    assert len(window.project.points) >= 30
    assert "已结束" in window.status_label.text()
    assert_restored(window)
    window._jump_to_frame(12)
    old = {point.frame: (point.x, point.y) for point in window.project.points}
    click_video(window, expected[12, 0] + 2, expected[12, 1])
    assert len(window.project.points) == len(old)
    assert window.project.points[12].source == "manual"
    assert all((point.x, point.y) == old[point.frame] for point in window.project.points if point.frame != 12)
    output = Path(__file__).resolve().parents[1] / "artifacts" / "ui-interaction"
    output.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(output / "synthetic-tracked.png"))
    assert not errors


def test_real_worker_loss_and_manual_recovery(editor, qt_app, tmp_path):
    window, errors = editor
    _, expected = load_synthetic(window, tmp_path, disappears_at=14)
    window.radius_spin.setValue(4)
    window._add_point(*expected[0])
    window._start_tracking()
    wait_for(qt_app, lambda: window._thread is None)
    assert window.project.points[-1].frame == 13
    assert "第 15 帧" in window.status_label.text()
    assert "可靠识别" in window.status_label.text()
    window._jump_to_frame(18)
    window._add_point(*expected[18])
    assert window.project.points[-1].frame == 18
    assert len(window.project.points) == 15
    assert_restored(window)
    assert not errors


def test_no_ball_at_seed_preserves_manual_anchor(editor, qt_app, tmp_path):
    window, errors = editor
    load_synthetic(window, tmp_path)
    window.radius_spin.setValue(4)
    window._add_point(8, 8)
    window._start_tracking()
    wait_for(qt_app, lambda: window._thread is None)
    assert len(window.project.points) == 1
    assert window.project.points[0].source == "manual"
    assert "起球帧未识别" in window.status_label.text()
    assert "原轨迹已保留" in window.status_label.text()
    assert_restored(window)
    assert not errors


def test_real_worker_cancellation_restores_controls(editor, qt_app, tmp_path):
    window, errors = editor
    _, expected = load_synthetic(window, tmp_path, count=128)
    window.radius_spin.setValue(4)
    window._add_point(*expected[0])
    window._start_tracking()
    wait_for(qt_app, lambda: window.progress_bar.value() >= 2)
    assert window._thread is not None
    QTest.mouseClick(window.cancel_button, Qt.MouseButton.LeftButton)
    wait_for(qt_app, lambda: window._thread is None)
    assert "已取消" in window.status_label.text()
    assert len(window.project.points) >= 1
    assert_restored(window)
    assert not errors


def test_export_button_runs_real_encoder_and_blocks_source_overwrite(editor, qt_app, tmp_path, monkeypatch):
    window, errors = editor
    path, expected = load_synthetic(window, tmp_path)
    window._add_point(*expected[0])
    window._jump_to_frame(31)
    window._add_point(*expected[31])
    output = tmp_path / "带轨迹.mp4"
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    QTest.mouseClick(window.export_button, Qt.MouseButton.LeftButton)
    assert window._thread is not None
    wait_for(qt_app, lambda: window._thread is None)
    assert output.is_file()
    capture = cv2.VideoCapture(str(output))
    try:
        assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 32
        assert int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) == 384
        assert int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 256
    finally:
        capture.release()
    assert "导出完成" in window.status_label.text()
    assert "无音频" in window.status_label.text()
    assert_restored(window)
    assert not errors
    # A source ending in .mp4 exercises the exact guard before the real exporter.
    window.project.video_path = str(output)
    window._export()
    assert errors[-1][0] == "请选择其他文件名"
    assert window._thread is None
    window.project.video_path = str(path)


def test_close_waits_for_actual_worker_cancellation(editor, qt_app, tmp_path):
    window, errors = editor
    _, expected = load_synthetic(window, tmp_path, count=128)
    window.radius_spin.setValue(4)
    window._add_point(*expected[0])
    window._start_tracking()
    wait_for(qt_app, lambda: window.progress_bar.value() >= 2)
    window.close()
    wait_for(qt_app, lambda: window._thread is None and not window.isVisible())
    assert not errors


def test_compatibility_copy_menu_runs_real_encoder_and_loads_copy(editor, qt_app, tmp_path, monkeypatch):
    window, errors = editor
    source, _ = load_synthetic(window, tmp_path)
    output = tmp_path / "兼容副本.mp4"
    monkeypatch.setattr(gui.QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    assert window.normalize_action.isEnabled()
    window.normalize_action.trigger()
    assert window._thread is not None
    assert not window.normalize_action.isEnabled()
    wait_for(qt_app, lambda: window._thread is None)
    assert source.is_file()
    assert output.is_file()
    assert window.project.video_path == str(output.resolve())
    assert window.info.frame_count == 32
    assert "兼容副本已载入" in window.status_label.text()
    assert "实际 32 帧" in window.status_label.text()
    assert not window.project.points
    assert_restored(window)
    assert not errors
