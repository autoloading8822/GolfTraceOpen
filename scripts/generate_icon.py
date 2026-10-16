"""Rasterize the project's own vector mark for Windows application resources."""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

root = Path(__file__).resolve().parents[1]
destination = root / "src" / "golftrace" / "assets"
destination.mkdir(parents=True, exist_ok=True)
image = QImage(256, 256, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#173d2e"))
painter.drawRoundedRect(QRectF(3, 3, 250, 250), 64, 64)
curve = QPainterPath()
curve.moveTo(52, 184)
curve.cubicTo(72, 94, 130, 48, 184, 76)
curve.cubicTo(216, 98, 212, 142, 198, 173)
painter.setPen(QPen(QColor("#78e1b7"), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
painter.setBrush(Qt.BrushStyle.NoBrush)
painter.drawPath(curve)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#ffffff"))
painter.drawEllipse(179, 154, 38, 38)
painter.end()
for suffix, format_name in (("png", "PNG"), ("ico", "ICO")):
    output = destination / f"golftrace.{suffix}"
    if not image.save(str(output), format_name):
        raise RuntimeError(f"Unable to write {output}")
    print(output)
