"""Shared data exchanged by tracking, editing, and export."""

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration(self) -> float:
        return self.frame_count / self.fps if self.fps > 0 else 0.0


@dataclass
class TrackPoint:
    frame: int
    x: float
    y: float
    confidence: float = 1.0
    source: str = "manual"


@dataclass
class TrackResult:
    info: VideoInfo
    points: list[TrackPoint]
    stop_reason: str
    last_frame: int
    # Each matrix maps the tracking start frame into this frame's image plane.
    transforms: dict[int, list[list[float]]] = field(default_factory=dict)


@dataclass
class Project:
    video_path: str = ""
    points: list[TrackPoint] = field(default_factory=list)
    transforms: dict[int, list[list[float]]] = field(default_factory=dict)
    color: str = "#42e0ad"
    line_width: int = 5
    schema_version: int = 1

    def save(self, path: str) -> None:
        import json
        import uuid

        destination = Path(path)
        payload = asdict(self)
        temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    @classmethod
    def load(cls, path: str) -> "Project":
        import json
        import math

        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if payload.get("schema_version") != 1:
            raise ValueError("不支持的项目文件版本")
        points = []
        for item in payload.get("points", []):
            point = TrackPoint(**item)
            if type(point.frame) is not int or point.frame < 0 or not all(math.isfinite(v) for v in (point.x, point.y, point.confidence)):
                raise ValueError("项目文件包含无效的轨迹点")
            points.append(point)
        transforms = {}
        for key, matrix in payload.get("transforms", {}).items():
            if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
                raise ValueError("项目文件包含无效的相机变换")
            if not all(math.isfinite(value) for row in matrix for value in row):
                raise ValueError("项目文件包含无效的相机变换")
            transforms[int(key)] = matrix
        color = payload.get("color", "#42e0ad")
        if not isinstance(color, str) or len(color) != 7 or color[0] != "#":
            raise ValueError("项目文件包含无效颜色")
        try:
            int(color[1:], 16)
        except ValueError as exc:
            raise ValueError("项目文件包含无效颜色") from exc
        width = int(payload.get("line_width", 5))
        if not 1 <= width <= 24:
            raise ValueError("项目文件包含无效线宽")
        return cls(str(payload.get("video_path", "")), sorted(points, key=lambda p: p.frame), transforms, color, width)
