"""Prepare the current UI for the Windows launcher; never touch Studio project data."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


class PreparationError(RuntimeError):
    pass


def fingerprint(files: list[Path], base: Path) -> str:
    result = hashlib.sha256()
    for path in sorted(files):
        result.update(path.relative_to(base).as_posix().encode("utf-8"))
        result.update(b"\0")
        result.update(path.read_bytes())
        result.update(b"\0")
    return result.hexdigest()


def source_files(web: Path) -> list[Path]:
    files = [web / name for name in ("index.html", "package.json", "package-lock.json", "vite.config.ts")]
    files.extend(web.glob("tsconfig*.json"))
    for directory in ("src", "public"):
        files.extend(path for path in (web / directory).rglob("*") if path.is_file())
    # The UI imports the shared, public build identifier.
    files.append(web.parents[1] / "resources" / "app-build.json")
    return files


def read_marker(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_marker(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def prepare_web(root: Path) -> str:
    web = root / "apps" / "web"
    dist = web / "dist"
    # Fingerprint against root because build metadata lives outside apps/web.
    source_hash = fingerprint(source_files(web), root)
    marker_path = dist / ".studio-build.json"
    marker = read_marker(marker_path)
    outputs = marker.get("outputs", {})
    current = marker.get("source_sha256") == source_hash and isinstance(outputs, dict) and "index.html" in outputs
    if current:
        for name, checksum in outputs.items():
            path = (dist / name).resolve()
            if not path.is_relative_to(dist.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                current = False
                break
    if current:
        print("Giao diện đã cập nhật; không cần build lại.")
        return "CURRENT"

    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if not npm:
        raise PreparationError("Cần Node.js 24 để cập nhật giao diện. Cài từ https://nodejs.org/ rồi mở lại Start-Studio.cmd.")
    dependency_hash = fingerprint([web / "package.json", web / "package-lock.json"], root)
    dependency_marker = web / "node_modules" / ".studio-dependencies.json"
    if read_marker(dependency_marker).get("dependencies_sha256") != dependency_hash or not (web / "node_modules" / "vite" / "package.json").is_file():
        print("Đang chuẩn bị thư viện giao diện; lần đầu hoặc khi dependency thay đổi có thể cần Internet.", flush=True)
        result = subprocess.run([npm, "ci", "--prefer-offline", "--no-audit", "--no-fund"], cwd=web, check=False)
        if result.returncode:
            raise PreparationError("Không cài được thư viện giao diện. Xem lỗi phía trên, kiểm tra kết nối rồi mở lại launcher.")
        write_marker(dependency_marker, {"dependencies_sha256": dependency_hash})

    print("Mã giao diện đã thay đổi hoặc bản build thiếu/hỏng. Đang cập nhật giao diện…", flush=True)
    result = subprocess.run([npm, "run", "build"], cwd=web, check=False)
    if result.returncode or not (dist / "index.html").is_file():
        raise PreparationError("Build giao diện thất bại; không khởi chạy với giao diện cũ. Xem lỗi phía trên.")
    if fingerprint(source_files(web), root) != source_hash:
        raise PreparationError("Mã nguồn thay đổi trong khi build. Mở lại launcher để cập nhật đúng phiên bản.")
    inventory = {path.relative_to(dist).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(dist.rglob("*")) if path.is_file() and path.name not in (".studio-build.json", ".studio-build.tmp")}
    write_marker(marker_path, {"schema_version": 1, "source_sha256": source_hash, "outputs": inventory})
    return "REBUILT"


def main() -> int:
    try:
        prepare_web(Path(__file__).resolve().parents[1])
    except (PreparationError, OSError, ValueError) as exc:
        print(f"Không chuẩn bị được giao diện: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
