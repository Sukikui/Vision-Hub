#!/usr/bin/env python3
"""Export a YOLO model to NCNN and install it for Vision-Hub."""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_DIR = Path("models/person-detector/yolo11n-ncnn")


@dataclass(frozen=True)
class NcnnExportFiles:
    """Resolved NCNN export file pair.

    Attributes:
        param_path: Path to the exported NCNN `.param` file.
        bin_path: Path to the exported NCNN `.bin` file.
    """

    param_path: Path
    bin_path: Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional argument list. When `None`, argparse reads `sys.argv`.

    Returns:
        Parsed command-line namespace.
    """

    parser = argparse.ArgumentParser(
        description="Export a YOLO model to NCNN and install the artifacts in Vision-Hub's model directory.",
    )
    parser.add_argument(
        "--model",
        default="yolo11n.pt",
        help="Ultralytics model name or local .pt path. Default: yolo11n.pt",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Install directory for model.ncnn.param/bin. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="YOLO export image size. Default: 640",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing model.ncnn.param/bin in the output directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the NCNN export and installation command.

    Args:
        argv: Optional argument list. When `None`, argparse reads `sys.argv`.

    Returns:
        Process exit code. `0` means success, `1` means failure.
    """

    args = parse_args(argv)
    try:
        _assert_output_available(args.output_dir, force=args.force)
        yolo_cls = _load_ultralytics_yolo()
        export_path = _export_to_ncnn(yolo_cls, model=args.model, imgsz=args.imgsz)
        exported_files = _resolve_ncnn_files(export_path)
        installed_files = _install_ncnn_files(exported_files, args.output_dir, force=args.force)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"NCNN model installed in: {installed_files.param_path.parent}")
    print(f"- {installed_files.param_path}")
    print(f"- {installed_files.bin_path}")
    return 0


def _load_ultralytics_yolo() -> Any:
    """Import the Ultralytics YOLO class lazily.

    Returns:
        The `ultralytics.YOLO` class.

    Raises:
        RuntimeError: If Ultralytics is not installed in the current command
            environment.
    """

    try:
        from ultralytics import YOLO
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ultralytics is required only for export. Run with: "
            "uv run --with ultralytics python tools/export_yolo_ncnn.py"
        ) from exc
    return YOLO


def _export_to_ncnn(yolo_cls: Any, *, model: str, imgsz: int) -> Path:
    """Export a YOLO model to NCNN with Ultralytics.

    Args:
        yolo_cls: Ultralytics `YOLO` class.
        model: Model name or local `.pt` path.
        imgsz: Export image size.

    Returns:
        Path to the Ultralytics NCNN export output.

    Raises:
        ValueError: If `imgsz` is not positive.
        FileNotFoundError: If the export output cannot be found.
    """

    if imgsz <= 0:
        raise ValueError("--imgsz must be > 0")

    yolo_model = yolo_cls(model)
    export_result = yolo_model.export(format="ncnn", imgsz=imgsz)
    return _resolve_export_path(export_result, model)


def _resolve_export_path(export_result: Any, model: str) -> Path:
    """Resolve the output directory returned or created by Ultralytics.

    Args:
        export_result: Value returned by `YOLO.export(...)`.
        model: Model name or local `.pt` path used for fallback naming.

    Returns:
        Existing path containing the NCNN export.

    Raises:
        FileNotFoundError: If no candidate export path exists.
    """

    candidates: list[Path] = []

    if isinstance(export_result, str | Path):
        candidates.append(Path(export_result))
    elif isinstance(export_result, list | tuple):
        candidates.extend(Path(item) for item in export_result if isinstance(item, str | Path))

    candidates.append(Path(f"{Path(model).stem}_ncnn_model"))

    for candidate in candidates:
        if candidate.exists():
            return candidate

    formatted = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"could not find NCNN export output; checked: {formatted}")


def _resolve_ncnn_files(path: Path) -> NcnnExportFiles:
    """Find NCNN `.param` and `.bin` files in an export path.

    Args:
        path: Export directory or direct `.param` path.

    Returns:
        Resolved NCNN export file pair.

    Raises:
        ValueError: If the path is ambiguous or not a `.param` file.
        FileNotFoundError: If the matching `.bin` file is missing.
    """

    if path.is_file():
        if path.suffix != ".param":
            raise ValueError(f"expected a .param file or directory, got: {path}")
        return _pair_from_param(path)

    preferred = (
        path / "model.ncnn.param",
        path / "yolo11n.ncnn.param",
        path / "yolo11s.ncnn.param",
    )
    for param_path in preferred:
        if param_path.exists():
            return _pair_from_param(param_path)

    param_files = sorted(path.glob("*.param"))
    if len(param_files) != 1:
        raise ValueError(f"expected exactly one .param file in NCNN export directory: {path}")
    return _pair_from_param(param_files[0])


def _pair_from_param(param_path: Path) -> NcnnExportFiles:
    """Resolve a `.param` file and its sibling `.bin` file.

    Args:
        param_path: Path to the NCNN `.param` file.

    Returns:
        Resolved NCNN export file pair.

    Raises:
        FileNotFoundError: If the sibling `.bin` file is missing.
    """

    bin_path = param_path.with_suffix(".bin")
    if not bin_path.exists():
        raise FileNotFoundError(f"missing NCNN .bin file beside {param_path}")
    return NcnnExportFiles(param_path=param_path, bin_path=bin_path)


def _install_ncnn_files(files: NcnnExportFiles, output_dir: Path, *, force: bool) -> NcnnExportFiles:
    """Copy NCNN artifacts into Vision-Hub's stable model directory.

    Args:
        files: Exported NCNN files to install.
        output_dir: Destination directory used by the Vision-Hub runtime.
        force: Whether existing destination files may be overwritten.

    Returns:
        Installed NCNN file paths.

    Raises:
        FileExistsError: If destination files already exist and `force` is
            false.
    """

    output_dir.mkdir(parents=True, exist_ok=True)

    target_param = output_dir / "model.ncnn.param"
    target_bin = output_dir / "model.ncnn.bin"
    for target in (target_param, target_bin):
        if target.exists() and not force:
            raise FileExistsError(f"{target} already exists; pass --force to overwrite it")

    shutil.copy2(files.param_path, target_param)
    shutil.copy2(files.bin_path, target_bin)
    return NcnnExportFiles(param_path=target_param, bin_path=target_bin)


def _assert_output_available(output_dir: Path, *, force: bool) -> None:
    """Fail early when destination model files already exist.

    Args:
        output_dir: Destination directory used by the Vision-Hub runtime.
        force: Whether existing destination files may be overwritten.

    Raises:
        FileExistsError: If destination files already exist and `force` is
            false.
    """

    if force:
        return

    for target in (output_dir / "model.ncnn.param", output_dir / "model.ncnn.bin"):
        if target.exists():
            raise FileExistsError(f"{target} already exists; pass --force to overwrite it")


if __name__ == "__main__":
    raise SystemExit(main())
