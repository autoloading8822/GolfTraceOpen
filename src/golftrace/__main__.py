"""Launch the GolfTrace Open desktop editor."""

import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback

from . import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="GolfTrace Open — 本地高尔夫视频轨迹编辑器")
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help="以离屏方式验证桌面启动并写出截图与 smoke.json")
    parser.add_argument("--video", metavar="PATH", help="冒烟测试中载入的真实视频")
    parser.add_argument("--exercise", action="store_true", help="在冒烟测试中真实运行选球、跟踪、手改、保存和后台导出")
    parser.add_argument("--native-smoke", action="store_true", help="验收时使用 Windows 原生窗口平台；仅与 --smoke-test 配合")
    parser.add_argument("--seed-x", type=float, default=0.0, help="验收起点的原图 x 坐标")
    parser.add_argument("--seed-y", type=float, default=0.0, help="验收起点的原图 y 坐标")
    parser.add_argument("--seed-frame", type=int, default=0, help="验收起点的帧编号，从 0 开始")
    parser.add_argument("--ball-radius", type=int, default=4, help="验收用球半径，原图像素，默认 4")
    args = parser.parse_args()
    if args.exercise and not (args.smoke_test and args.video):
        parser.error("--exercise 必须同时提供 --smoke-test 和 --video")
    if args.native_smoke and not args.smoke_test:
        parser.error("--native-smoke 必须提供 --smoke-test")
    output_dir = Path(args.smoke_test).resolve() if args.smoke_test else None
    if output_dir is not None:
        os.environ["QT_QPA_PLATFORM"] = "windows" if args.native_smoke else "offscreen"
        output_dir.mkdir(parents=True, exist_ok=True)

    def write_result(payload: dict) -> None:
        if output_dir is not None:
            (output_dir / "smoke.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from .gui import MainWindow, create_application
        from PySide6.QtGui import QIcon

        app = create_application([sys.argv[0]])
        job_results = {}
        window = MainWindow()
        icon_path = Path(__file__).parent / "assets" / "golftrace.ico"
        if icon_path.is_file():
            icon = QIcon(str(icon_path))
            app.setWindowIcon(icon)
            window.setWindowIcon(icon)
        if output_dir is not None:
            from PySide6.QtCore import QTimer

            errors = []

            def raise_dialog_error(title: str, details: str) -> None:
                if args.exercise:
                    errors.append(f"{title}: {details}")
                    return
                raise RuntimeError(f"{title}: {details}")

            window._error = raise_dialog_error
            if args.video:
                from .models import Project

                video = str(Path(args.video).resolve())
                if not window._load_video(video, Project(video_path=video)):
                    raise RuntimeError("指定视频无法载入")

            def finish_smoke() -> None:
                try:
                    screenshot = output_dir / "screenshot.png"
                    if not window.grab().save(str(screenshot)):
                        raise RuntimeError("离屏截图保存失败")
                    write_result({
                        "success": True,
                        "version": __version__,
                        "platform": sys.platform,
                        "qt_platform": app.platformName(),
                        "window_visible": window.isVisible(),
                        "video_loaded": window.info is not None,
                        "video_path": window.project.video_path or None,
                        "window_size": [window.width(), window.height()],
                        "screenshot": str(screenshot),
                    })
                    app.exit(0)
                except Exception:
                    write_result({"success": False, "version": __version__, "error": traceback.format_exc()})
                    app.exit(1)

            if args.exercise:
                _schedule_exercise(app, window, args, output_dir, write_result, errors, job_results)
            else:
                QTimer.singleShot(350, finish_smoke)
        window.show()
        return app.exec()
    except Exception:
        details = traceback.format_exc()
        write_result({"success": False, "version": __version__, "error": details})
        if output_dir is None:
            sys.stderr.write(details)
        return 1


def _schedule_exercise(app, window, args, output_dir, write_result, errors, job_results):
    """Instrument the actual application workflow without replacing processing.

    Only modal dialog presentation is adapted for unattended QA. MainWindow's
    point handler, QThreads, tracking, project save, exporter and cleanup run.
    """
    import math

    import cv2
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from .models import Project

    started = time.monotonic()
    state = {"stage": "start", "error": None, "failure_at": None}
    evidence = {"exercise": True}
    output_video = output_dir / "exercise.mp4"
    project_path = output_dir / "exercise.golftrace.json"
    timer = QTimer(window)
    timer.setInterval(25)
    original_information = QMessageBox.information
    QMessageBox.information = lambda *unused, **kwargs: QMessageBox.StandardButton.Ok

    def finish(success: bool, error: str | None = None, *, forced_exit=False):
        timer.stop()
        screenshot = output_dir / "screenshot.png"
        screenshot_saved = window.grab().save(str(screenshot))
        payload = {
            "success": success and screenshot_saved,
            "version": __version__,
            "platform": sys.platform,
            "qt_platform": app.platformName(),
            "window_visible": window.isVisible(),
            "video_loaded": window.info is not None,
            "video_path": window.project.video_path or None,
            "window_size": [window.width(), window.height()],
            "screenshot": str(screenshot) if screenshot_saved else None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stage": state["stage"],
            **evidence,
        }
        if error or not screenshot_saved:
            payload["error"] = error or "离屏截图保存失败"
        if forced_exit:
            payload["forced_exit"] = True
            payload["worker_finished"] = False
        else:
            payload["worker_finished"] = window._thread is None
        write_result(payload)
        QMessageBox.information = original_information
        if forced_exit:
            # A hung decoder cannot be reported as a cleanly stopped worker.
            # Record the failure first, then bound this dedicated QA process.
            os._exit(2)
        app.exit(0 if payload["success"] else 1)

    def fail(details: str):
        if state["error"] is None:
            state["error"] = details
            state["failure_at"] = time.monotonic()
        if window._thread is not None:
            window._cancel_job()
        else:
            finish(False, state["error"])

    def advance():
        try:
            if state["error"] is not None:
                if window._thread is None:
                    finish(False, state["error"])
                elif time.monotonic() - state["failure_at"] > 15:
                    finish(False, state["error"] + "\n取消后 15 秒后台仍未结束，验收进程被强制终止。", forced_exit=True)
                return
            if errors:
                fail("\n".join(errors))
                return
            if time.monotonic() - started > 60:
                fail(f"验收超过 60 秒，阶段：{state['stage']}")
                return
            if state["stage"] == "start":
                if window.info is None:
                    raise RuntimeError("验收视频尚未载入")
                if not 0 <= args.seed_frame < window.info.frame_count:
                    raise ValueError("--seed-frame 超出视频范围")
                if not all(math.isfinite(value) for value in (args.seed_x, args.seed_y)):
                    raise ValueError("起点坐标必须是有限数值")
                if not (0 <= args.seed_x < window.info.width and 0 <= args.seed_y < window.info.height):
                    raise ValueError("起点坐标超出视频原图范围")
                if not 2 <= args.ball_radius <= 40:
                    raise ValueError("--ball-radius 必须位于 2 至 40")
                window._jump_to_frame(args.seed_frame)
                if window.current_frame != args.seed_frame:
                    raise RuntimeError("无法定位到验收起始帧")
                window.radius_spin.setValue(args.ball_radius)
                window.canvas.point_selected.emit(args.seed_x, args.seed_y)
                if window._seed is None or len(window.project.points) != 1:
                    raise RuntimeError("点选起球位置没有产生手动起点")
                window._start_tracking()
                if window._thread is None:
                    raise RuntimeError("真实跟踪后台线程未启动")
                window._worker.succeeded.connect(lambda result: job_results.__setitem__("tracking", result))
                state["stage"] = "tracking"
            elif state["stage"] == "tracking":
                if window._thread is not None:
                    return
                result = job_results.get("tracking")
                if result is None:
                    raise RuntimeError("未收到真实跟踪结果")
                evidence["tracked_count"] = len(result.points)
                evidence["tracking_stop_reason"] = result.stop_reason
                if len(result.points) < 2:
                    raise RuntimeError(f"真实跟踪只获得 {len(result.points)} 个点，不能继续导出验收")
                original = {point.frame: (point.x, point.y) for point in window.project.points}
                anchor = result.points[len(result.points) // 2]
                window._jump_to_frame(anchor.frame)
                x = max(0.0, min(window.info.width - 1.0, anchor.x + 1.0))
                y = max(0.0, min(window.info.height - 1.0, anchor.y + 1.0))
                window.canvas.point_selected.emit(x, y)
                manual = next(point for point in window.project.points if point.frame == anchor.frame)
                if manual.source != "manual" or (manual.x, manual.y) != (x, y):
                    raise RuntimeError("真实手动修正未替换当前帧点")
                if len(window.project.points) != len(original) or any(
                    (point.x, point.y) != original[point.frame]
                    for point in window.project.points if point.frame != anchor.frame
                ):
                    raise RuntimeError("手动修正改变了其他帧轨迹点")
                evidence["manual_edit"] = True
                evidence["manual_edit_frame"] = anchor.frame
                window.project_path = str(project_path)
                if not window._save_project():
                    raise RuntimeError("真实项目保存失败")
                saved = Project.load(str(project_path))
                if len(saved.points) != len(original) or not any(
                    point.frame == anchor.frame and point.source == "manual" and (point.x, point.y) == (x, y)
                    for point in saved.points
                ):
                    raise RuntimeError("保存后重新读取的项目没有保留修正结果")
                evidence["project_saved"] = True
                evidence["project_path"] = str(project_path)
                original_dialog = QFileDialog.getSaveFileName
                try:
                    QFileDialog.getSaveFileName = lambda *unused, **kwargs: (str(output_video), "")
                    window._export()
                finally:
                    QFileDialog.getSaveFileName = original_dialog
                if window._thread is None:
                    raise RuntimeError("真实导出后台线程未启动")
                window._worker.succeeded.connect(lambda result: job_results.__setitem__("export", result))
                state["stage"] = "exporting"
            elif state["stage"] == "exporting":
                if window._thread is not None:
                    return
                encoded = job_results.get("export")
                if not isinstance(encoded, dict):
                    raise RuntimeError("未收到真实编码器导出结果")
                if encoded.get("audio_preserved") is not False:
                    raise RuntimeError("编码器没有明确报告无音频导出状态")
                if not output_video.is_file():
                    raise RuntimeError("导出结果文件不存在")
                capture = cv2.VideoCapture(str(output_video))
                frame_count = 0
                try:
                    if not capture.isOpened():
                        raise RuntimeError("导出 MP4 无法重新打开")
                    while True:
                        ok, frame = capture.read()
                        if not ok:
                            break
                        if frame is None or frame.size == 0:
                            raise RuntimeError("导出视频存在空帧")
                        frame_count += 1
                finally:
                    capture.release()
                if frame_count < 2 or frame_count != encoded.get("frame_count"):
                    raise RuntimeError(f"导出逐帧回读数量 {frame_count} 与编码器结果 {encoded.get('frame_count')} 不符")
                evidence["export"] = {
                    "path": str(output_video),
                    "frame_count": frame_count,
                    "readable": True,
                    "audio_preserved": encoded["audio_preserved"],
                }
                if not window.import_button.isEnabled() or not window.export_button.isEnabled():
                    raise RuntimeError("处理结束后界面控件未恢复")
                evidence["controls_restored"] = True
                state["stage"] = "complete"
                finish(True)
        except Exception:
            fail(traceback.format_exc())

    timer.timeout.connect(advance)
    write_result({"success": False, "version": __version__, "exercise": True, "in_progress": True, "stage": "scheduled"})
    QTimer.singleShot(350, lambda: timer.start())


if __name__ == "__main__":
    raise SystemExit(main())
