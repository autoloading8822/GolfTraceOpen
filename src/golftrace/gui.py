"""Native desktop editor for a local, reviewable golf-ball trace."""

from __future__ import annotations

import os
import threading
import traceback
from pathlib import Path
from typing import Callable

import cv2
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .models import Project, TrackPoint, TrackResult, VideoInfo
from .render import draw_trace, export_video, normalize_video
from .tracking import inspect_video, read_frame, track_video


STYLE = """
QMainWindow, QWidget#root { background: #f4f6f3; color: #203a32; }
QWidget { font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-size: 12px; color: #203a32; }
QLabel { background: transparent; }
QLabel#brand { font-size: 25px; font-weight: 700; letter-spacing: -0.5px; }
QLabel#subtitle, QLabel#muted { color: #75857d; }
QLabel#eyebrow { color: #718078; font-size: 10px; font-weight: 600; letter-spacing: 1.4px; }
QLabel#sectionTitle { font-size: 14px; font-weight: 650; }
QLabel#badge { background: #e3eee7; color: #44705b; border-radius: 10px; padding: 5px 10px; font-size: 10px; }
QLabel#emptyTitle { font-size: 25px; font-weight: 650; color: #dfeae4; }
QLabel#emptySub { color: #92a89e; font-size: 13px; }
QLabel#emptyStep { color: #c1d0c7; font-size: 12px; }
QLabel#bigGlyph { font-size: 58px; color: #7be2b9; }
QLabel#notice { color: #6d7c73; font-size: 11px; }
QFrame#card { background: white; border: 1px solid #dfe7df; border-radius: 12px; }
QFrame#viewer { background: #14271f; border-radius: 15px; }
QFrame#transport { background: white; border: 1px solid #dfe7df; border-radius: 10px; }
QFrame#line { background: #e5ebe4; min-height: 1px; max-height: 1px; }
QPushButton { background: #ffffff; border: 1px solid #d7e1d8; border-radius: 7px; padding: 9px 12px; font-weight: 550; }
QPushButton:hover { background: #edf5ee; border-color: #a6c6b3; }
QPushButton:pressed { background: #e0ede3; }
QPushButton:disabled { background: #f3f5f1; border-color: #e3e8e0; color: #a2aca4; }
QPushButton#primary { background: #1d503e; color: #ffffff; border: 1px solid #1d503e; }
QPushButton#primary:hover { background: #2a6650; }
QPushButton#primary:disabled { background: #b3c8bb; color: #eff4ef; border-color: #b3c8bb; }
QPushButton#mint { background: #7be2b9; color: #133d2c; border: 1px solid #7be2b9; }
QPushButton#mint:hover { background: #9aebcc; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #61776a; padding: 6px 8px; }
QPushButton#danger { color: #a04d38; }
QSpinBox { background: #f9fbf7; border: 1px solid #dce4da; border-radius: 5px; padding: 6px; min-width: 65px; }
QSpinBox:disabled { color: #a2aca4; }
QSlider::groove:horizontal { height: 4px; background: #e1e9df; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #2d7053; border-radius: 2px; }
QSlider::handle:horizontal { width: 13px; margin: -5px 0; background: #2d7053; border: 2px solid white; border-radius: 7px; }
QProgressBar { background: #e8eee5; border: 0; height: 6px; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: #3b9771; border-radius: 3px; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { width: 7px; background: transparent; }
QScrollBar::handle:vertical { background: #cdd9ce; border-radius: 3px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenuBar { background: #f4f6f3; color: #61776a; }
QMenuBar::item:selected { background: #e1ebe1; }
QMenu { background: #ffffff; border: 1px solid #dce4da; padding: 5px; }
QMenu::item { padding: 7px 22px; }
QMenu::item:selected { background: #e8f2e9; }
"""


class VideoCanvas(QWidget):
    """Letterboxed video with clicks mapped to original video pixels."""

    point_selected = Signal(float, float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(380, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._pixmap: QPixmap | None = None
        self._source_width = 0
        self._source_height = 0
        self._marker: tuple[float, float] | None = None
        self._image_rect = QRectF()

    def set_frame(self, frame_bgr, marker: tuple[float, float] | None = None) -> None:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(image)
        self._source_width, self._source_height = width, height
        self._marker = marker
        self.update()

    def image_rect(self) -> QRectF:
        if not self._source_width or not self._source_height:
            return QRectF()
        factor = min(self.width() / self._source_width, self.height() / self._source_height)
        width, height = self._source_width * factor, self._source_height * factor
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def map_to_video(self, position: QPointF) -> tuple[float, float] | None:
        rect = self.image_rect()
        if rect.isEmpty() or not rect.contains(position):
            return None
        x = (position.x() - rect.left()) * self._source_width / rect.width()
        y = (position.y() - rect.top()) * self._source_height / rect.height()
        return min(self._source_width - 1.0, max(0.0, x)), min(self._source_height - 1.0, max(0.0, y))

    def mousePressEvent(self, event) -> None:
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            coordinates = self.map_to_video(event.position())
            if coordinates is not None:
                self.point_selected.emit(*coordinates)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#14271f"))
        if self._pixmap is None:
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._image_rect = self.image_rect()
        painter.drawPixmap(self._image_rect, self._pixmap, QRectF(self._pixmap.rect()))
        if self._marker:
            x, y = self._marker
            px = self._image_rect.left() + x / self._source_width * self._image_rect.width()
            py = self._image_rect.top() + y / self._source_height * self._image_rect.height()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#0c2419"), 4))
            painter.drawEllipse(QPointF(px, py), 8, 8)
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.drawEllipse(QPointF(px, py), 8, 8)
            painter.drawLine(QPointF(px - 13, py), QPointF(px - 5, py))
            painter.drawLine(QPointF(px + 5, py), QPointF(px + 13, py))
            painter.drawLine(QPointF(px, py - 13), QPointF(px, py - 5))
            painter.drawLine(QPointF(px, py + 5), QPointF(px, py + 13))


class _Worker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(int, int)
    finished = Signal()

    def __init__(self, operation: Callable):
        super().__init__()
        self.operation = operation
        self.cancel_event = threading.Event()

    def progress(self, current, total=None, *unused) -> None:
        if total is None:
            value = float(current)
            self.progress_changed.emit(int(value * 100) if 0 <= value <= 1 else int(value), 100)
        else:
            self.progress_changed.emit(int(current), max(1, int(total)))

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(self.progress, self.cancel_event.is_set)
        except Exception:
            self.failed.emit(traceback.format_exc())
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"GolfTrace Open {__version__}")
        self.resize(1240, 820)
        self.setMinimumSize(1000, 680)
        self.project = Project()
        self.info: VideoInfo | None = None
        self.project_path: str | None = None
        self.current_frame = 0
        self._frame_bgr = None
        self._seed: TrackPoint | None = None
        self._dirty = False
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._job_kind = ""
        self._normalize_ready: tuple[str, int] | None = None
        self._pending_close = False
        self._seek_failed = False
        self._last_directory = str(Path.home() / "Videos")
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._next_playback_frame)
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(70)
        self._seek_timer.timeout.connect(lambda: self._show_frame(self.timeline.value()))
        self._make_ui()
        self._make_menu()
        self._sync_controls()

    def _make_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 16, 26, 16)
        outer.setSpacing(18)

        header = QHBoxLayout()
        identity = QVBoxLayout()
        identity.setSpacing(4)
        eyebrow = QLabel("GOLF VIDEO WORKSPACE")
        eyebrow.setObjectName("eyebrow")
        identity.addWidget(eyebrow)
        brand_row = QHBoxLayout()
        brand = QLabel("GolfTrace Open")
        brand.setObjectName("brand")
        brand_row.addWidget(brand)
        version = QLabel(f"WINDOWS  /  {__version__}")
        version.setObjectName("badge")
        brand_row.addWidget(version)
        brand_row.addStretch()
        identity.addLayout(brand_row)
        subtitle = QLabel("本地视频 · 可核对的球路 · 逐帧编辑")
        subtitle.setObjectName("subtitle")
        identity.addWidget(subtitle)
        header.addLayout(identity, 1)
        self.open_project_button = self._button("打开项目", self._open_project)
        self.save_button = self._button("保存项目", self._save_project)
        self.import_button = self._button("＋ 导入视频", self._import_video, "primary")
        header.addWidget(self.open_project_button)
        header.addWidget(self.save_button)
        header.addWidget(self.import_button)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(20)
        preview_column = QVBoxLayout()
        preview_column.setSpacing(12)
        video_header = QHBoxLayout()
        self.video_name = QLabel("视频预览")
        self.video_name.setObjectName("sectionTitle")
        self.video_detail = QLabel("尚未导入视频")
        self.video_detail.setObjectName("muted")
        video_header.addWidget(self.video_name, 1)
        video_header.addWidget(self.video_detail)
        preview_column.addLayout(video_header)

        self.viewer = QFrame()
        self.viewer.setObjectName("viewer")
        viewer_layout = QVBoxLayout(self.viewer)
        viewer_layout.setContentsMargins(2, 2, 2, 2)
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self._empty_view())
        self.canvas = VideoCanvas()
        self.canvas.point_selected.connect(self._add_point)
        self.preview_stack.addWidget(self.canvas)
        viewer_layout.addWidget(self.preview_stack)
        preview_column.addWidget(self.viewer, 1)

        transport = QFrame()
        transport.setObjectName("transport")
        transport_layout = QVBoxLayout(transport)
        transport_layout.setContentsMargins(15, 11, 15, 11)
        transport_layout.setSpacing(9)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.valueChanged.connect(self._schedule_seek)
        self.timeline.sliderPressed.connect(self._stop_playback)
        self.timeline.sliderReleased.connect(self._seek_released)
        transport_layout.addWidget(self.timeline)
        controls = QHBoxLayout()
        self.previous_button = self._button("上一帧", lambda: self._step_frame(-1))
        self.play_button = self._button("播放", self._toggle_playback, "primary")
        self.next_button = self._button("下一帧", lambda: self._step_frame(1))
        controls.addWidget(self.previous_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.next_button)
        controls.addStretch()
        self.frame_spin = QSpinBox()
        self.frame_spin.setPrefix("第 ")
        self.frame_spin.setSuffix(" 帧")
        self.frame_spin.setRange(1, 1)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.valueChanged.connect(lambda frame: self._jump_to_frame(frame - 1))
        controls.addWidget(self.frame_spin)
        self.time_label = QLabel("00:00.000 / 00:00.000")
        self.time_label.setObjectName("muted")
        controls.addWidget(self.time_label)
        transport_layout.addLayout(controls)
        preview_column.addWidget(transport)
        hint = QLabel("点击画面标记当前帧球心；← / → 逐帧，空格播放，Delete 删除当前帧点。")
        hint.setObjectName("notice")
        hint.setWordWrap(True)
        preview_column.addWidget(hint)
        body.addLayout(preview_column, 1)

        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFixedWidth(294)
        panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 6, 0)
        panel_layout.setSpacing(12)
        card, layout = self._card("01", "选球与自动跟踪")
        self.seed_label = self._text("先停在起球帧，再点击球心。", "muted")
        layout.addWidget(self.seed_label)
        radius_row = QHBoxLayout()
        radius_row.addWidget(QLabel("球半径（原图像素）"), 1)
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(2, 40)
        self.radius_spin.setValue(6)
        self.radius_spin.setSuffix(" px")
        radius_row.addWidget(self.radius_spin)
        layout.addLayout(radius_row)
        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("跟踪至"), 1)
        self.end_spin = QSpinBox()
        self.end_spin.setRange(1, 1)
        self.end_spin.setPrefix("第 ")
        self.end_spin.setSuffix(" 帧")
        self.end_spin.setKeyboardTracking(False)
        end_row.addWidget(self.end_spin)
        layout.addLayout(end_row)
        self.track_button = self._button("开始自动跟踪", self._start_tracking, "primary")
        layout.addWidget(self.track_button)
        layout.addWidget(self._text("球太小、遮挡或模糊可能丢失，结果需核对；可逐帧补点。重新跟踪会替换整条轨迹。", "notice"))
        panel_layout.addWidget(card)

        card, layout = self._card("02", "逐帧校正与外观")
        self.point_label = self._text("当前帧暂无轨迹点。", "muted")
        layout.addWidget(self.point_label)
        self.remove_point_button = self._button("删除当前帧点", self._remove_current_point)
        layout.addWidget(self.remove_point_button)
        style_row = QHBoxLayout()
        self.color_button = self._button("●  轨迹颜色", self._choose_color)
        style_row.addWidget(self.color_button, 1)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 24)
        self.width_spin.setSuffix(" px")
        self.width_spin.setValue(self.project.line_width)
        self.width_spin.valueChanged.connect(self._change_width)
        style_row.addWidget(self.width_spin)
        layout.addLayout(style_row)
        self.clear_button = self._button("清空轨迹", self._clear_trace, "quiet")
        layout.addWidget(self.clear_button)
        layout.addWidget(self._text("逐帧点选可补点或修正，其他轨迹点保留。", "notice"))
        panel_layout.addWidget(card)

        card, layout = self._card("03", "保存与导出")
        self.export_button = self._button("导出 MP4（无音频）", self._export, "primary")
        layout.addWidget(self.export_button)
        layout.addWidget(self._text("按原尺寸、原帧率导出。项目保存轨迹与外观，视频需单独保留。", "notice"))
        panel_layout.addWidget(card)
        panel_layout.addStretch()
        panel_scroll.setWidget(panel)
        body.addWidget(panel_scroll)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel("准备就绪 · 视频在本机处理")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(150)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        footer.addWidget(self.progress_bar)
        self.cancel_button = self._button("取消", self._cancel_job)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        self.total_label = QLabel("0 个轨迹点")
        self.total_label.setObjectName("muted")
        footer.addWidget(self.total_label)
        outer.addLayout(footer)
        self._update_color_button()

        QShortcut(QKeySequence("Space"), self, activated=self._toggle_playback)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._step_frame(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._step_frame(1))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, activated=self._remove_current_point)

    def _empty_view(self) -> QWidget:
        empty = QWidget()
        layout = QVBoxLayout(empty)
        layout.setContentsMargins(28, 30, 28, 30)
        layout.setSpacing(14)
        layout.addStretch()
        glyph = QLabel("◎")
        glyph.setObjectName("bigGlyph")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(glyph)
        title = QLabel("把球路，留在画面里")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        sub = QLabel("离线处理高尔夫视频，自动跟踪后逐帧核对。")
        sub.setObjectName("emptySub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(8)
        for text in ("01   导入本地击球视频", "02   停在起球帧，点击选中球心", "03   跟踪、校正，导出带轨迹的视频"):
            label = QLabel(text)
            label.setObjectName("emptyStep")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        layout.addSpacing(12)
        self.empty_import_button = self._button("选择本地视频", self._import_video, "mint")
        self.empty_import_button.setFixedWidth(180)
        layout.addWidget(self.empty_import_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        return empty

    @staticmethod
    def _text(text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _button(text: str, callback, object_name: str = "") -> QPushButton:
        button = QPushButton(text)
        if object_name:
            button.setObjectName(object_name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _card(number: str, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(10)
        heading = QLabel(f"{number}   {title}")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        return card, layout

    def _make_menu(self) -> None:
        menu = self.menuBar().addMenu("文件")
        self.file_actions: list[QAction] = []
        for text, shortcut, callback in (
            ("导入视频…", "Ctrl+O", self._import_video),
            ("打开项目…", "Ctrl+Shift+O", self._open_project),
            ("保存项目", "Ctrl+S", self._save_project),
            ("项目另存为…", "Ctrl+Shift+S", lambda: self._save_project(save_as=True)),
        ):
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            menu.addAction(action)
            self.file_actions.append(action)
        self.normalize_action = QAction("生成兼容视频副本…", self)
        self.normalize_action.triggered.connect(self._normalize_video)
        menu.addAction(self.normalize_action)
        menu.addSeparator()
        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.close)
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("关于 GolfTrace Open").triggered.connect(self._about)

    def _about(self) -> None:
        QMessageBox.information(self, "关于 GolfTrace Open", f"GolfTrace Open {__version__}\n\n本地高尔夫视频轨迹编辑工具。\n初版使用视觉跟踪并支持手动补点；自动轨迹需要核对。\n导出 MP4 暂不保留音频。\n\n轨迹是画面中的像素位置，不代表三维弹道或实际飞行距离。")

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_title()

    def _update_title(self) -> None:
        name = Path(self.project_path).name if self.project_path else "未命名项目"
        self.setWindowTitle(f"{'* ' if self._dirty else ''}{name} — GolfTrace Open {__version__}")

    def _maybe_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(self, "保存项目", "当前轨迹或外观尚未保存。是否先保存项目？", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return self._save_project() if answer == QMessageBox.StandardButton.Save else True

    def _import_video(self) -> None:
        if self._thread or not self._maybe_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入高尔夫视频", self._last_directory, "视频文件 (*.mp4 *.mov *.m4v *.avi *.mkv *.webm);;所有文件 (*)")
        if path:
            self._load_video(path, Project(video_path=str(Path(path).resolve())))

    def _load_video(self, path: str, project: Project, project_path: str | None = None) -> bool:
        self._stop_playback()
        try:
            info = inspect_video(path)
            frame = read_frame(path, 0)
            if frame is None:
                raise ValueError("视频第 1 帧无法读取")
            for point in project.points:
                if point.frame >= info.frame_count or not (0 <= point.x < info.width and 0 <= point.y < info.height):
                    raise ValueError("项目轨迹超出视频范围，请确认选择了原始视频")
        except Exception as exc:
            self._error("无法打开视频", str(exc))
            return False
        self.project = project
        self.project.video_path = str(Path(path).resolve())
        self.info = info
        self.project_path = project_path
        self.current_frame = 0
        self._frame_bgr = frame
        self._seed = project.points[0] if project.points else None
        self._dirty = False
        self._last_directory = str(Path(path).parent)
        self._seek_failed = False
        self.preview_stack.setCurrentWidget(self.canvas)
        self.timeline.blockSignals(True)
        self.timeline.setRange(0, max(0, info.frame_count - 1))
        self.timeline.setValue(0)
        self.timeline.blockSignals(False)
        self.frame_spin.blockSignals(True)
        self.frame_spin.setRange(1, info.frame_count)
        self.frame_spin.setValue(1)
        self.frame_spin.blockSignals(False)
        self.end_spin.setRange(self._seed.frame + 1 if self._seed else 1, info.frame_count)
        self.end_spin.setValue(info.frame_count)
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(project.line_width)
        self.width_spin.blockSignals(False)
        name = Path(path).name
        self.video_name.setText(name if len(name) <= 48 else name[:45] + "…")
        self.video_name.setToolTip(str(Path(path).resolve()))
        self.video_detail.setText(f"{info.width} × {info.height}  ·  {info.fps:.2f} fps")
        self._update_color_button()
        self._update_title()
        self._refresh_frame()
        self._sync_controls()
        self._set_status("视频已就绪 · 停在起球帧后点击球心")
        return True

    def _open_project(self) -> None:
        if self._thread or not self._maybe_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开轨迹项目", self._last_directory, "GolfTrace 项目 (*.golftrace.json);;JSON 文件 (*.json)")
        if not path:
            return
        try:
            project = Project.load(path)
            video_path = Path(project.video_path)
            relocated = False
            if not video_path.is_absolute():
                video_path = Path(path).parent / video_path
            if not video_path.is_file():
                QMessageBox.information(self, "重新定位视频", "项目记录的视频已移动或无法找到。请选择同一个原始视频以继续。")
                replacement, _ = QFileDialog.getOpenFileName(self, "重新定位原始视频", str(Path(path).parent), "视频文件 (*.mp4 *.mov *.m4v *.avi *.mkv *.webm);;所有文件 (*)")
                if not replacement:
                    return
                video_path = Path(replacement)
                relocated = True
            if self._load_video(str(video_path), project, path) and relocated:
                self._mark_dirty()
                self._set_status("已重新定位原始视频 · 保存项目以记住新路径")
        except Exception as exc:
            self._error("无法读取项目", str(exc))

    def _save_project(self, checked=False, save_as: bool = False) -> bool:
        if self._thread or self.info is None:
            return False
        destination = self.project_path
        if destination is None or save_as:
            default = destination or str(Path(self.project.video_path).with_suffix(".golftrace.json"))
            destination, _ = QFileDialog.getSaveFileName(self, "保存轨迹项目", default, "GolfTrace 项目 (*.golftrace.json)")
            if not destination:
                return False
            if not destination.lower().endswith(".golftrace.json"):
                destination += ".golftrace.json"
                if Path(destination).exists() and not self._confirm_replace(destination):
                    return False
        try:
            if Path(destination).resolve() == Path(self.project.video_path).resolve():
                raise ValueError("项目文件不能覆盖原始视频")
            self.project.save(destination)
        except Exception as exc:
            self._error("保存失败", str(exc))
            return False
        self.project_path = destination
        self._dirty = False
        self._update_title()
        self._set_status(f"项目已保存 · {Path(destination).name}")
        return True

    def _schedule_seek(self, frame: int) -> None:
        if self.info is not None and self._thread is None:
            self._stop_playback()
            self._seek_timer.start()

    def _seek_released(self) -> None:
        self._seek_timer.stop()
        self._show_frame(self.timeline.value())

    def _jump_to_frame(self, frame: int) -> None:
        if self.info is None or self._thread:
            return
        self._stop_playback()
        self._seek_timer.stop()
        self._show_frame(frame)

    def _step_frame(self, delta: int) -> None:
        if self.info is not None and self._thread is None:
            self._jump_to_frame(min(self.info.frame_count - 1, max(0, self.current_frame + delta)))

    def _show_frame(self, frame_index: int) -> bool:
        if self.info is None:
            return False
        frame_index = min(self.info.frame_count - 1, max(0, frame_index))
        try:
            frame = read_frame(self.project.video_path, frame_index)
            if frame is None:
                raise ValueError(f"第 {frame_index + 1} 帧无法读取")
        except Exception as exc:
            self._stop_playback()
            self._set_status(f"读取失败 · {exc} · 可从文件菜单生成兼容视频副本")
            self.timeline.blockSignals(True)
            self.timeline.setValue(self.current_frame)
            self.timeline.blockSignals(False)
            self.frame_spin.blockSignals(True)
            self.frame_spin.setValue(self.current_frame + 1)
            self.frame_spin.blockSignals(False)
            if not self._seek_failed:
                self._seek_failed = True
                self._error("视频帧读取失败", str(exc))
            return False
        self._seek_failed = False
        self.current_frame = frame_index
        self._frame_bgr = frame
        self.timeline.blockSignals(True)
        self.timeline.setValue(frame_index)
        self.timeline.blockSignals(False)
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(frame_index + 1)
        self.frame_spin.blockSignals(False)
        self._refresh_frame()
        self._sync_controls()
        return True

    def _refresh_frame(self) -> None:
        if self._frame_bgr is None or self.info is None:
            return
        point = next((item for item in self.project.points if item.frame == self.current_frame), None)
        rendered = draw_trace(self._frame_bgr.copy(), self.current_frame, self.project.points, transforms=self.project.transforms, color=self.project.color, line_width=self.project.line_width)
        self.canvas.set_frame(rendered, (point.x, point.y) if point else None)
        self.time_label.setText(f"{self._time(self.current_frame / self.info.fps)} / {self._time(self.info.duration)}")
        if point:
            origin = "手动" if point.source == "manual" else "自动"
            self.point_label.setText(f"第 {self.current_frame + 1} 帧 · {origin}点\n球心 ({point.x:.1f}, {point.y:.1f})")
        else:
            self.point_label.setText(f"第 {self.current_frame + 1} 帧暂无轨迹点。\n点击画面可补点。")
        self.total_label.setText(f"{len(self.project.points)} 个轨迹点")

    @staticmethod
    def _time(seconds: float) -> str:
        milliseconds = max(0, int(round(seconds * 1000)))
        return f"{milliseconds // 60000:02d}:{milliseconds // 1000 % 60:02d}.{milliseconds % 1000:03d}"

    def _toggle_playback(self) -> None:
        if self.info is None or self._thread:
            return
        if self._play_timer.isActive():
            self._stop_playback()
        else:
            self._seek_timer.stop()
            if self.current_frame >= self.info.frame_count - 1 and not self._show_frame(0):
                return
            self._play_timer.start(max(1, round(1000 / self.info.fps)))
            self.play_button.setText("暂停")

    def _stop_playback(self) -> None:
        self._play_timer.stop()
        if hasattr(self, "play_button"):
            self.play_button.setText("播放")

    def _next_playback_frame(self) -> None:
        if self.info is None or self.current_frame >= self.info.frame_count - 1:
            self._stop_playback()
            return
        self._show_frame(self.current_frame + 1)

    def _add_point(self, x: float, y: float) -> None:
        if self.info is None or self._thread:
            return
        self._stop_playback()
        point = TrackPoint(self.current_frame, x, y, source="manual")
        self.project.points = sorted([item for item in self.project.points if item.frame != self.current_frame] + [point], key=lambda item: item.frame)
        self._seed = point
        self.end_spin.setMinimum(point.frame + 1)
        self._mark_dirty()
        self._refresh_frame()
        self._sync_controls()
        self._set_status(f"已标记第 {self.current_frame + 1} 帧球心 · 可继续补点或从这里自动跟踪")

    def _remove_current_point(self) -> None:
        if self.info is None or self._thread:
            return
        remaining = [point for point in self.project.points if point.frame != self.current_frame]
        if len(remaining) == len(self.project.points):
            return
        self.project.points = remaining
        if self._seed and self._seed.frame == self.current_frame:
            self._seed = None
        self._mark_dirty()
        self._refresh_frame()
        self._sync_controls()
        self._set_status(f"已删除第 {self.current_frame + 1} 帧轨迹点")

    def _clear_trace(self) -> None:
        if not self.project.points or self._thread:
            return
        answer = QMessageBox.question(self, "清空轨迹", "清空当前项目的全部轨迹点？原始视频不受影响。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.project.points.clear()
        self.project.transforms.clear()
        self._seed = None
        self._mark_dirty()
        self._refresh_frame()
        self._sync_controls()
        self._set_status("轨迹已清空 · 可重新选球")

    def _choose_color(self) -> None:
        if self._thread:
            return
        color = QColorDialog.getColor(QColor(self.project.color), self, "选择轨迹颜色")
        if color.isValid():
            self.project.color = color.name()
            self._mark_dirty()
            self._update_color_button()
            self._refresh_frame()

    def _update_color_button(self) -> None:
        self.color_button.setStyleSheet(f"QPushButton {{ border-left: 7px solid {self.project.color}; }}")

    def _change_width(self, width: int) -> None:
        if self._thread:
            return
        self.project.line_width = width
        if self.info is not None:
            self._mark_dirty()
            self._refresh_frame()

    def _start_tracking(self) -> None:
        if self.info is None or self._seed is None or self._thread:
            return
        self._stop_playback()
        self._seek_timer.stop()
        if len(self.project.points) > 1:
            answer = QMessageBox.question(self, "重新自动跟踪", "新的自动跟踪将替换整条轨迹，包括已有的手动修正点。建议先保存项目。是否继续？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        seed = self._seed
        path = self.project.video_path
        radius = self.radius_spin.value()
        end = self.end_spin.value() - 1
        self._launch_job("tracking", "正在自动跟踪", lambda progress, cancelled: track_video(path, seed.frame, (seed.x, seed.y), ball_radius=radius, end_frame=end, progress=progress, cancelled=cancelled))

    def _export(self) -> None:
        if self.info is None or len(self.project.points) < 2 or self._thread:
            return
        self._stop_playback()
        default = str(Path(self.project.video_path).with_name(Path(self.project.video_path).stem + "_trace.mp4"))
        destination, _ = QFileDialog.getSaveFileName(self, "导出 MP4（无音频）", default, "MP4 视频 (*.mp4)")
        if not destination:
            return
        if not destination.lower().endswith(".mp4"):
            destination += ".mp4"
            if Path(destination).exists() and not self._confirm_replace(destination):
                return
        if Path(destination).resolve() == Path(self.project.video_path).resolve():
            self._error("请选择其他文件名", "导出视频不能覆盖原始视频。")
            return
        project = self.project
        self._export_destination = destination
        self._launch_job("export", "正在导出 MP4（无音频）", lambda progress, cancelled: export_video(project.video_path, destination, project, progress=progress, cancelled=cancelled))

    def _normalize_video(self) -> None:
        if self.info is None or self._thread or not self._maybe_discard():
            return
        self._stop_playback()
        answer = QMessageBox.question(self, "生成兼容视频副本", "将顺序解码并按标称帧率生成 MP4（无音频）。可变帧率视频的播放时间可能稍有变化。\n\n原始文件会保留。完成后载入兼容副本并开始新的轨迹项目。是否继续？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        source = self.project.video_path
        default = str(Path(source).with_name(Path(source).stem + "_compatible.mp4"))
        destination, _ = QFileDialog.getSaveFileName(self, "保存兼容视频副本（无音频）", default, "MP4 视频 (*.mp4)")
        if not destination:
            return
        if not destination.lower().endswith(".mp4"):
            destination += ".mp4"
            if Path(destination).exists() and not self._confirm_replace(destination):
                return
        if Path(destination).resolve() == Path(source).resolve():
            self._error("请选择其他文件名", "兼容视频副本不能覆盖原始视频。")
            return
        self._normalize_destination = destination
        self._launch_job("normalize", "正在生成兼容副本（无音频）", lambda progress, cancelled: normalize_video(source, destination, progress=progress, cancelled=cancelled))

    def _launch_job(self, kind: str, title: str, operation: Callable) -> None:
        self._stop_playback()
        self._seek_timer.stop()
        self._job_kind = kind
        self._normalize_ready = None
        self._thread = QThread(self)
        self._worker = _Worker(operation)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress_changed.connect(self._job_progress)
        self._worker.succeeded.connect(self._job_succeeded)
        self._worker.failed.connect(self._job_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._job_finished)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.cancel_button.setText("取消")
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self._set_status(title + " · 可随时取消")
        self._sync_controls()
        self._thread.start()

    @Slot(int, int)
    def _job_progress(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(min(current, total))

    @Slot(object)
    def _job_succeeded(self, result) -> None:
        if self._job_kind == "tracking":
            if not isinstance(result, TrackResult):
                self._job_failed("跟踪器返回了无法识别的结果")
                return
            reason = str(result.stop_reason).lower()
            if not result.points:
                if "cancel" in reason:
                    self._set_status("跟踪已取消 · 原轨迹已保留")
                elif "lost" in reason:
                    self._set_status("起球帧未识别到可跟踪的白球 · 原轨迹已保留，请调整球位或半径")
                else:
                    self._set_status(f"未得到自动轨迹 · {result.stop_reason} · 原轨迹已保留")
                return
            self.project.points = sorted(result.points, key=lambda point: point.frame)
            self.project.transforms = result.transforms
            self._mark_dirty()
            if "cancel" in reason:
                status = "跟踪已取消 · 已保留取消前的轨迹点"
            elif "lost" in reason:
                lost_at = min(self.info.frame_count, result.points[-1].frame + 2) if result.points else result.last_frame + 1
                status = f"第 {lost_at} 帧起未再可靠识别球位 · 可逐帧补点"
            elif reason in {"end", "completed", "complete", "end_frame", "end_of_video", "finished", "eof"}:
                status = f"自动跟踪已结束 · 共 {len(result.points)} 个点，请核对轨迹"
            else:
                status = f"自动跟踪已停止 · {result.stop_reason} · 共 {len(result.points)} 个点"
            self._show_frame(min(result.last_frame, self.info.frame_count - 1))
            self._set_status(status)
        elif self._job_kind == "normalize":
            if isinstance(result, dict) and (result.get("cancelled") or result.get("status") == "cancelled"):
                self._set_status("生成兼容副本已取消")
                return
            count = int(result.get("frame_count", 0)) if isinstance(result, dict) else 0
            self._normalize_ready = (self._normalize_destination, count)
            self._set_status(f"兼容副本已生成 · 实际 {count} 帧 · 无音频")
        else:
            if isinstance(result, dict) and (result.get("cancelled") or result.get("status") == "cancelled"):
                self._set_status("导出已取消")
                return
            frames = result.get("frame_count", "") if isinstance(result, dict) else ""
            self._set_status(f"导出完成 · {frames} 帧 · 无音频 · {Path(self._export_destination).name}")
            if not self._pending_close:
                QMessageBox.information(self, "导出完成", f"已导出带轨迹的视频（无音频）：\n\n{self._export_destination}")

    @Slot(str)
    def _job_failed(self, details: str) -> None:
        cancelled = self._worker is not None and self._worker.cancel_event.is_set()
        if cancelled and ("cancel" in details.lower() or "取消" in details):
            self._set_status("操作已取消")
        else:
            hint = " · 可从文件菜单生成兼容视频副本后重试" if self._job_kind == "export" else ""
            self._set_status("操作失败 · 请查看错误详情" + hint)
            if not self._pending_close:
                self._error("处理失败", details)

    @Slot()
    def _job_finished(self) -> None:
        thread = self._thread
        # finished can be delivered while native thread-local cleanup is still
        # unwinding. Join it before dropping the final Python worker reference.
        if thread is not None and not thread.wait(3000):
            self._set_status("正在收束后台线程…")
            QTimer.singleShot(100, self._job_finished)
            return
        self._thread = None
        self._worker = None
        self._job_kind = ""
        if thread is not None:
            thread.deleteLater()
        self.progress_bar.hide()
        self.cancel_button.hide()
        self._sync_controls()
        if self._pending_close:
            self.setEnabled(True)
            QTimer.singleShot(0, self.close)
        elif self._normalize_ready is not None:
            destination, count = self._normalize_ready
            self._normalize_ready = None
            if self._load_video(destination, Project(video_path=destination)):
                self._set_status(f"兼容副本已载入 · 实际 {count} 帧 · 无音频 · 可重新选球")

    def _cancel_job(self) -> None:
        if self._worker:
            self._worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("正在取消…")
            self._set_status("正在取消 · 等待当前帧处理结束")

    def _sync_controls(self) -> None:
        busy = self._thread is not None
        loaded = self.info is not None
        ready = loaded and not busy
        for widget in (self.import_button, self.empty_import_button, self.open_project_button):
            widget.setEnabled(not busy)
        self.save_button.setEnabled(ready)
        for widget in (self.timeline, self.frame_spin, self.previous_button, self.next_button, self.play_button, self.radius_spin, self.end_spin, self.width_spin, self.color_button, self.canvas):
            widget.setEnabled(ready)
        has_current = any(point.frame == self.current_frame for point in self.project.points)
        self.remove_point_button.setEnabled(ready and has_current)
        self.clear_button.setEnabled(ready and bool(self.project.points))
        self.export_button.setEnabled(ready and len(self.project.points) >= 2)
        self.track_button.setEnabled(ready and self._seed is not None and self._seed.frame < self.info.frame_count - 1)
        if self._seed is not None:
            self.seed_label.setText(f"已选第 {self._seed.frame + 1} 帧球心\n({self._seed.x:.1f}, {self._seed.y:.1f})")
        else:
            self.seed_label.setText("先停在起球帧，再点击球心。")
        if hasattr(self, "file_actions"):
            for index, action in enumerate(self.file_actions):
                action.setEnabled(not busy if index < 2 else ready)
            self.normalize_action.setEnabled(ready)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def _confirm_replace(self, destination: str) -> bool:
        answer = QMessageBox.question(self, "覆盖已有文件", f"文件已存在：\n{destination}\n\n是否覆盖？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _error(self, title: str, details: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        lines = details.strip().splitlines()
        box.setText(lines[-1] if lines else title)
        if len(lines) > 1:
            box.setDetailedText(details)
        box.exec()

    def closeEvent(self, event) -> None:
        self._stop_playback()
        self._seek_timer.stop()
        if self._thread:
            self._pending_close = True
            self._cancel_job()
            self.setEnabled(False)
            event.ignore()
            return
        if self._maybe_discard():
            event.accept()
        else:
            self._pending_close = False
            self.setEnabled(True)
            event.ignore()


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or [])
    # Qt's Windows offscreen platform can start without a system font database.
    # Register existing system fonts so headless screenshots retain readable text.
    if os.name == "nt" and not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "msyh.ttc"):
            font_path = fonts / name
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setApplicationName("GolfTrace Open")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("GolfTrace Open")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    return app
