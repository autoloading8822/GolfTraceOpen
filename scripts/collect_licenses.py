"""Copy installed distribution license notices into a release directory."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys


def collect(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    packages = []
    for distribution in sorted(
        importlib.metadata.distributions(),
        key=lambda item: item.metadata.get("Name", "").lower(),
    ):
        name = distribution.metadata.get("Name", "unknown")
        target = destination / name
        copied = []
        for relative in distribution.files or []:
            filename = relative.name.lower()
            components = {part.lower() for part in relative.parts}
            is_license = (
                filename.startswith(("license", "licence", "copying", "notice"))
                or "licenses" in components
                or "licences" in components
            )
            if not is_license:
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file():
                continue
            safe_relative = Path(*[part for part in relative.parts if part not in ("..", ".")])
            output = target / safe_relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
            copied.append(output.relative_to(destination).as_posix())
        packages.append(
            {
                "name": name,
                "version": distribution.version,
                "license_expression": distribution.metadata.get("License-Expression"),
                "license_metadata": distribution.metadata.get("License"),
                "home_page": distribution.metadata.get("Home-page"),
                "project_urls": distribution.metadata.get_all("Project-URL", []),
                "copied_notices": copied,
            }
        )
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE"
    if not python_license.is_file():
        raise RuntimeError("Cannot locate the Python runtime license")
    shutil.copy2(python_license, destination / "PYTHON-LICENSE.txt")
    (destination / "installed-packages.json").write_text(
        json.dumps(packages, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Collected notices for {len(packages)} installed distributions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    collect(parser.parse_args().destination)
