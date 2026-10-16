"""Package the reviewed static site without deploying or changing its source.

Only the handoff ZIP, its companion instructions, and the local verification
record are written. All ZIP contents are assembled and decompressed in memory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
from urllib.parse import unquote, urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "website" / "dist"
TOP = "GolfTraceOpen-cloudflare"
ZIP_PATH = ROOT / "release" / f"{TOP}.zip"
INSTRUCTIONS_PATH = ROOT / "release" / "安装指令.md"
VERIFICATION_PATH = ROOT / "artifacts" / "cloudflare-package-verification.json"
APPROVED_FILES = {"_headers", "favicon.ico", "index.html", "release.json", "screenshot.png"}
WORKER_NAME = "golftrace-open-downloads"
COMPATIBILITY_DATE = "2026-10-16"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: set[str] = set()

    def handle_starttag(self, tag, attrs) -> None:
        for name, value in attrs:
            if name not in {"src", "href", "poster"} or not value:
                continue
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            self.references.add(unquote(parsed.path).lstrip("/"))


def validate_release(release: dict) -> None:
    if not isinstance(release.get("published"), bool):
        raise ValueError("release.json must explicitly specify published as a boolean")
    if not re.fullmatch(r"[0-9A-Za-z.+_-]+", str(release.get("version", ""))):
        raise ValueError("release.json has an invalid version")
    if not isinstance(release.get("files"), list):
        raise ValueError("release.json files must be an array")
    for item in release["files"]:
        if item.get("kind") not in {"installer", "portable"}:
            raise ValueError("release.json has an unrecognized artifact kind")
        name = item.get("name", "")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise ValueError("release.json has an invalid artifact filename")
        if not isinstance(item.get("bytes"), int) or item["bytes"] <= 0:
            raise ValueError("release.json has an invalid artifact size")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(item.get("sha256", ""))):
            raise ValueError("release.json has an invalid artifact hash")
        if release["published"] and not item.get("url"):
            raise ValueError("A published release must contain actual artifact URLs")
    for value in [release.get("source_url"), *(item.get("url") for item in release["files"])]:
        if value is None:
            continue
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Release URLs must be public HTTPS URLs without embedded credentials")


def snapshot_site() -> tuple[dict[str, bytes], dict, dict]:
    if not SITE.is_dir():
        raise FileNotFoundError("website/dist does not exist")
    paths = sorted(path for path in SITE.rglob("*") if path.is_file())
    names = {path.relative_to(SITE).as_posix() for path in paths}
    if names != APPROVED_FILES:
        raise ValueError("Static site files differ from the reviewed five-file scope; review before packaging")
    source: dict[str, bytes] = {}
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(SITE.resolve()):
            raise ValueError("Static assets must be regular files inside website/dist")
        name = path.relative_to(SITE).as_posix()
        data = path.read_bytes()
        if len(data) > 25 * 1024 * 1024:
            raise ValueError(f"Static asset exceeds the 25 MiB packaging boundary: {name}")
        source[name] = data
    release = json.loads(source["release.json"].decode("utf-8-sig"))
    validate_release(release)
    html = source["index.html"].decode("utf-8")
    parser = LocalReferences()
    parser.feed(html)
    parser.references.update(re.findall(r"fetch\(['\"]([^'\"]+)['\"]\)", html))
    missing = sorted(parser.references - names)
    if missing:
        raise ValueError("Missing local HTML/JavaScript assets: " + ", ".join(missing))
    for reference in parser.references:
        if ".." in PurePosixPath(reference).parts:
            raise ValueError("Static references must not leave the public root")
    secret_pattern = re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"AKIA[A-Z0-9]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
        r"[A-Z]:[\\/]Users[\\/]|[A-Z]:[\\/]Chatgpt_test[\\/])", re.I
    )
    for name, data in source.items():
        if name.endswith((".html", ".json")) or name == "_headers":
            if secret_pattern.search(data.decode("utf-8-sig")):
                raise ValueError(f"Potential private material in public asset; value not displayed: {name}")
    png = source["screenshot.png"]
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise ValueError("screenshot.png has an invalid PNG header")
    width, height = struct.unpack(">II", png[16:24])
    if not width or not height:
        raise ValueError("screenshot.png dimensions are invalid")
    icon = source["favicon.ico"]
    reserved, image_type, image_count = struct.unpack_from("<HHH", icon)
    if reserved or image_type != 1 or not image_count or len(icon) < 6 + image_count * 16:
        raise ValueError("favicon.ico has an invalid ICO directory")
    for index in range(image_count):
        image_bytes, offset = struct.unpack_from("<II", icon, 6 + index * 16 + 8)
        if not image_bytes or offset < 6 + image_count * 16 or offset + image_bytes > len(icon):
            raise ValueError("favicon.ico contains an invalid image offset")
    checks = {
        "local_references": sorted(parser.references),
        "all_local_references_present": True,
        "screenshot_png_header": {"width": width, "height": height},
        "favicon_ico_directory": {"images": image_count, "bounds_valid": True},
        "public_text_scan": "no_matched_private_material",
    }
    return source, release, checks


def build_instructions(source: dict[str, bytes], release: dict, entries: list[str]) -> bytes:
    extraction = ROOT / "release" / "cloudflare-handoff-extracted"
    deployment_root = extraction / TOP
    manifest_rows = []
    for item in release["files"]:
        url = item.get("url") or "未设置（null）"
        manifest_rows.append(
            f"- `{item['name']}`：{item['bytes']} 字节；URL：{url}；SHA-256：`{item['sha256']}`。"
        )
    if not manifest_rows:
        manifest_rows.append("- 发行清单未列出安装包或便携包。")
    published = str(release["published"]).lower()
    download_state = (
        "清单已启用下载链接；本打包脚本不执行公网下载，链接与安装包完整下载校验须由发布者另行确认。"
        if release["published"] else
        "清单尚未启用下载：网站保留“公开下载尚未开放”状态。不能仅修改 published 来冒充发布；应先完成 GitHub 实际发行与下载校验，再更新源清单并重新打包。"
    )
    listing = "\n".join(f"- `{TOP}/{entry}`" for entry in entries)
    doc = f"""# GolfTrace Open：Cloudflare 安装与发布交接

状态：**已打包，未部署**。Cloudflare 当前未登录，目标账号、正式 Worker 身份和网址均待确认。本包不构成覆盖任何现有站点的授权。

## 文件与部署根

- ZIP：`{ZIP_PATH}`。
- 解压顶层：`{TOP}/`。
- 按下列命令解压后的 Worker 配置根目录：`{deployment_root}`。
- 实际网站部署根目录：`{deployment_root / 'public'}`；ZIP 内精确相对路径为 `{TOP}/public/`。
- 入口：`{TOP}/public/index.html`。这是已构建的静态网站，无前端构建步骤。
- `wrangler.jsonc` 只上传 `./public`；`安装指令.md` 与 `FILES.sha256` 位于其外，不应成为网站公开资源。
- 拟议新 Worker：`{WORKER_NAME}`，不是已确认的空闲名称。配置日期：`{COMPATIBILITY_DATE}`。不含 routes 或 custom domain 配置。

## 当前发行快照

从这次打包实际读取的 `public/release.json` 得到：版本 `{release['version']}`，`published={published}`，`source_url={release.get('source_url') or 'null'}`。

{download_state}

{chr(10).join(manifest_rows)}

安装包和便携 ZIP 不放在本 Cloudflare 包内。网站通过发行清单中的实际 HTTPS 链接分发程序；后续改为 R2 时，应先验证真实下载地址再更新清单，不能把大安装包塞入 public。

## 图片与图标

- `{TOP}/public/screenshot.png`：实际程序界面图片。
- `{TOP}/public/favicon.ico`：网站图标。

## ZIP 内完整文件清单

下列清单由实际输入生成，并在最终 ZIP 写出后逐项比对；共 {len(entries)} 个文件。

{listing}

`FILES.sha256` 覆盖 public 中的全部 {len(source)} 个文件、wrangler.jsonc 和安装指令；为避免循环哈希，不包含它自身。ZIP 大小与最终 SHA-256 记录在同项目 `artifacts/cloudflare-package-verification.json`，不把 ZIP 自身校验值写进 ZIP。

## 本地预检

仅在新的空目录解压；已有人工修改目录应另选位置，不清空覆盖。

```powershell
$handoffExtractRoot = '{extraction}'
if (Test-Path -LiteralPath $handoffExtractRoot) {{ throw '目标已存在；请另选新的空目录，不覆盖旧内容。' }}
Expand-Archive -LiteralPath '{ZIP_PATH}' -DestinationPath $handoffExtractRoot
Set-Location -LiteralPath (Join-Path $handoffExtractRoot '{TOP}')
Get-Content -LiteralPath '.\\FILES.sha256' -Encoding UTF8 | ForEach-Object {{
    $parts = $_ -split '  ', 2
    if ($parts.Count -ne 2) {{ throw '无效校验清单行' }}
    $actualHash = (Get-FileHash -LiteralPath $parts[1] -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $parts[0]) {{ throw "校验失败：$($parts[1])" }}
}}
node --version
npm --version
npx wrangler --version
npx wrangler dev
```

若转移到另一台电脑，只调整上述绝对路径前缀，保留顶层目录和 public 层级。预览地址以 Wrangler 实际输出为准；桌面和手机宽度检查首页、图片、图标、滚动、下载状态及校验值复制。确认下载链接与清单一致，未发布时应保持准备中状态。本次打包未执行 Wrangler 预览或浏览器验收，不能把这些步骤记作已通过。

## Cloudflare 预检与保留旧站

在部署根目录执行 `npx wrangler whoami`，核实实际账号与所需权限；未登录时再执行 `npx wrangler login` 完成本机交互授权。不要复制或公开登录凭证。

先只读查看目标账号的 Workers & Pages、现有 routes 和 custom domains，记录当前旧站 URL、状态、关键资源和必要截图作为基线；旧站清单目前待确认，不虚构旧站网址。

检查 `{WORKER_NAME}` 是否已存在：可在控制台核对，并运行 `npx wrangler deployments list --name {WORKER_NAME}` 辅助检查。命令失败不等于名称空闲，必须区分认证失败、权限不足与确实不存在。若有同名现有项目，不部署覆盖；先取得用户对另一个新名称的确认，再调整本包配置。

本次范围仅为新增独立下载页。保留所有旧 Worker、Pages、DNS、页面与路由，不复用旧项目、不绑定既有域名。账号、Worker 名及目标发布方式确认并获得覆盖该动作的授权后，才执行部署。

## 部署与验收步骤（尚未执行）

```powershell
# 前提：当前目录是含 wrangler.jsonc 的 Worker 根，目标与授权已确认。
npx wrangler deploy --dry-run
npx wrangler deploy
```

记录 Wrangler 实际返回的 HTTPS URL、Worker 名与版本/部署 ID，不从账号名拼接网址。正式自定义域名仍待确认；首次可使用实际分配的独立 workers.dev 地址。

部署后完整 GET `/`、`/release.json`、`/screenshot.png` 与 `/favicon.ico`，检查响应状态、媒体类型，并将下载字节的 SHA-256 与 public 中对应文件逐项比对。`/_headers` 是平台配置文件，不按普通公开静态文件要求返回 200。检查手机与桌面浏览器，实际打开下载链接并核对安装包字节数和 SHA-256；仅 HEAD 不能证明安装包内容完整。

以下私有/非公开路径必须实际请求并返回 **404**，不能返回首页占位：`/.env`、`/.git/config`、`/wrangler.jsonc`、`/安装指令.md`、`/FILES.sha256`、`/research/private.md`、`/artifacts/smoke.json`、`/samples/private/test.mp4`、`/src/golftrace/gui.py`。本配置使用 `not_found_handling=none`，不启用 SPA 回退。

```powershell
$verifiedBaseUrl = Read-Host '粘贴本次部署输出的真实 HTTPS 根网址'
if (([uri]$verifiedBaseUrl).Scheme -ne 'https') {{ throw '必须是实际 HTTPS 地址' }}
$verifiedBaseUrl = $verifiedBaseUrl.TrimEnd('/')
$privatePaths = @('/.env', '/.git/config', '/wrangler.jsonc', '/安装指令.md', '/FILES.sha256', '/research/private.md', '/artifacts/smoke.json', '/samples/private/test.mp4', '/src/golftrace/gui.py')
foreach ($privatePath in $privatePaths) {{
    $status = & curl.exe --silent --show-error --output NUL --write-out '%{{http_code}}' ($verifiedBaseUrl + [uri]::EscapeUriString($privatePath))
    if ($LASTEXITCODE -ne 0 -or $status -ne '404') {{ throw "私有路径未通过404检查：$privatePath" }}
}}
```

最后复查预检记录中的全部旧站入口与关键资源，比较状态、内容及截图；旧站回归失败时保留证据并定位影响，不能宣称无影响。完成认证、实际部署、HTTPS 资源哈希、私有路径 404、浏览器操作、下载包校验和旧站回归后，才能将 Cloudflare 状态改为已部署并已验证。

## 交给 Codex 的可复制指令

推荐模型：**gpt-6.1-sol**。

智慧等级：**Medium（中）**。

推荐理由：适合既有静态文件的配置、独立部署与逐项验证；本任务无需重新设计程序或算法。名称和等级沿用当前 Codex 可用选项，API 模型选择文档只作一般取舍参考，不代表 API 型号与 Codex 别名相同。

```text
接手 GolfTrace Open 静态下载站交付包。先阅读同目录安装指令，并逐项核对 FILES.sha256 和 ZIP 实际清单。
Worker 根目录是 {deployment_root}，静态根是其 public 子目录；只上传 public。
当前状态是已打包、未部署。拟议新 Worker 为 {WORKER_NAME}，账号与正式网址待确认，Cloudflare 尚需本机登录。
先完成可独立进行的本地预检；核实账号权限、同名项目和旧站基线。若已有该名字，不覆盖，取得新目标确认。
保留全部旧 Worker、Pages、DNS、路由与页面；不要接入旧域名。获得明确目标和发布授权后，再按文档部署独立站。
严格按本包 release.json 的实际 published 与 URL 呈现下载，不捏造可用链接，不上传视频、私人日志、源码或大型安装包至 public。
部署后执行HTTPS页面/图片/图标/清单的逐文件哈希、私有路径404、桌面和手机浏览器操作、实际安装包下载校验及旧站回归。
回报实际URL、部署ID、每项验证证据、失败与未验证事项；只有全部必要步骤完成才写已部署并已验证。
```

参考：[Cloudflare Workers Static Assets 官方指南](https://developers.cloudflare.com/workers/static-assets/get-started/)、[静态资源配置](https://developers.cloudflare.com/workers/static-assets/binding/)、[OpenAI 模型选择原则](https://developers.openai.com/api/docs/guides/model-selection)。
"""
    return doc.encode("utf-8")


def main() -> None:
    source, release, checks = snapshot_site()
    configuration = {
        "$schema": "./node_modules/wrangler/config-schema.json",
        "name": WORKER_NAME,
        "compatibility_date": COMPATIBILITY_DATE,
        "workers_dev": True,
        "assets": {"directory": "./public", "not_found_handling": "none"},
    }
    entries = sorted([*(f"public/{name}" for name in source), "wrangler.jsonc", "安装指令.md", "FILES.sha256"])
    instructions = build_instructions(source, release, entries)
    contents = {f"public/{name}": data for name, data in source.items()}
    contents["wrangler.jsonc"] = (json.dumps(configuration, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    contents["安装指令.md"] = instructions
    contents["FILES.sha256"] = ("\n".join(f"{sha256(data)}  {name}" for name, data in sorted(contents.items())) + "\n").encode("utf-8")
    source_fingerprint = sha256(json.dumps(
        {name: sha256(data) for name, data in sorted(contents.items())}, sort_keys=True,
    ).encode("utf-8"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(f"{TOP}/{name}", date_time=(2026, 10, 16, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    package = buffer.getvalue()
    previous = json.loads(VERIFICATION_PATH.read_text("utf-8")) if VERIFICATION_PATH.is_file() else None
    reproducible = None
    if previous and previous.get("input_fingerprint") == source_fingerprint:
        reproducible = previous.get("zip", {}).get("sha256") == sha256(package)
        if not reproducible:
            raise RuntimeError("Identical inputs produced different ZIP bytes")
    # Recheck source bytes before writing, so a concurrent source edit cannot be
    # silently represented as the current site in this handoff.
    if any((SITE / name).read_bytes() != data for name, data in source.items()):
        raise RuntimeError("website/dist changed during packaging; rerun after its update completes")
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    VERIFICATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    ZIP_PATH.write_bytes(package)
    INSTRUCTIONS_PATH.write_bytes(instructions)
    with zipfile.ZipFile(ZIP_PATH, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Final ZIP failed CRC verification")
        actual_names = archive.namelist()
        expected_names = [f"{TOP}/{name}" for name in sorted(contents)]
        if actual_names != expected_names or len(set(actual_names)) != len(actual_names):
            raise RuntimeError("Final ZIP has an unexpected file list")
        for name, data in contents.items():
            if archive.read(f"{TOP}/{name}") != data:
                raise RuntimeError(f"Final ZIP decompressed-byte mismatch: {name}")
        for line in archive.read(f"{TOP}/FILES.sha256").decode("utf-8").splitlines():
            digest, name = line.split("  ", 1)
            if sha256(archive.read(f"{TOP}/{name}")) != digest:
                raise RuntimeError(f"Manifest hash mismatch: {name}")
        if archive.read(f"{TOP}/安装指令.md") != INSTRUCTIONS_PATH.read_bytes():
            raise RuntimeError("ZIP and companion instructions differ")
    record = {
        "status": "PACKAGED_NOT_DEPLOYED",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_fingerprint": source_fingerprint,
        "repeat_run_same_inputs_same_zip": reproducible,
        "zip": {"path": str(ZIP_PATH), "bytes": ZIP_PATH.stat().st_size,
                "sha256": sha256(ZIP_PATH.read_bytes()), "file_count": len(contents)},
        "zip_crc_ok": True,
        "zip_exact_file_list_ok": True,
        "zip_decompressed_bytes_match": True,
        "manifest_all_listed_hashes_ok": True,
        "instructions_identical": True,
        "source_files": [{"path": name, "bytes": len(data), "sha256": sha256(data)}
                         for name, data in sorted(source.items())],
        "zip_files": [{"path": f"{TOP}/{name}", "bytes": len(data), "sha256": sha256(data)}
                      for name, data in sorted(contents.items())],
        "static_checks": checks,
        "release_manifest": release,
        "cloudflare": {"worker_name_proposed": WORKER_NAME, "target_confirmed": False,
                       "authentication": "not_logged_in_per_handoff_context_not_checked_by_script",
                       "deployed": False, "public_url": None},
        "unverified": ["Wrangler configuration dry run", "Wrangler/browser local preview",
                       "Cloudflare authentication and name collision check", "actual Cloudflare deployment",
                       "live HTTPS resource hashes", "private URL 404 responses",
                       "live desktop/mobile interaction", "artifact URL full-download verification",
                       "existing-site regression"],
    }
    VERIFICATION_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": record["status"], **record["zip"],
                      "published": release["published"],
                      "repeat_run_same_inputs_same_zip": reproducible}, ensure_ascii=False))


if __name__ == "__main__":
    main()
