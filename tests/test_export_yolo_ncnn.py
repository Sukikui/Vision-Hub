"""Tests for the YOLO-to-NCNN export helper."""

import tempfile
import unittest
from pathlib import Path

from tools.export_yolo_ncnn import (
    _install_ncnn_files,
    _prepare_model_argument,
    _resolve_existing_model_path,
    _resolve_ncnn_files,
    _temporary_working_directory,
)


class ExportYoloNcnnTest(unittest.TestCase):
    """Unit tests for export-helper filesystem behavior."""

    def test_resolves_existing_model_path(self) -> None:
        """Resolve a local model path to an absolute path."""

        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "model.pt"
            model_path.write_bytes(b"")

            resolved = _resolve_existing_model_path(str(model_path))

            self.assertEqual(resolved, model_path.resolve())

    def test_keeps_ultralytics_model_names_unresolved(self) -> None:
        """Keep model names as-is so Ultralytics can download them."""

        model_name = "missing-yolo-model-name.pt"

        self.assertFalse(Path(model_name).exists())
        self.assertIsNone(_resolve_existing_model_path(model_name))

    def test_copies_existing_model_into_temp_export_dir(self) -> None:
        """Copy a local `.pt` beside the temporary Ultralytics export."""

        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as export_dir:
            source_model = Path(source_dir) / "model.pt"
            source_model.write_bytes(b"weights")

            prepared = _prepare_model_argument(
                str(source_model),
                local_model_path=source_model.resolve(),
                export_dir=Path(export_dir),
            )

            prepared_path = Path(prepared)
            self.assertEqual(prepared_path.parent, Path(export_dir))
            self.assertEqual(prepared_path.read_bytes(), b"weights")

    def test_keeps_missing_model_name_for_ultralytics(self) -> None:
        """Pass missing model names through to Ultralytics unchanged."""

        self.assertEqual(
            _prepare_model_argument(
                "yolo11n.pt",
                local_model_path=None,
                export_dir=Path("/tmp"),
            ),
            "yolo11n.pt",
        )

    def test_resolves_optional_metadata_yaml(self) -> None:
        """Resolve Ultralytics metadata beside the NCNN param file."""

        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = Path(temp_dir)
            (export_dir / "model.ncnn.param").write_text("", encoding="utf-8")
            (export_dir / "model.ncnn.bin").write_bytes(b"weights")
            metadata_path = export_dir / "metadata.yaml"
            metadata_path.write_text("task: detect\n", encoding="utf-8")

            files = _resolve_ncnn_files(export_dir)

            self.assertEqual(files.metadata_path, metadata_path)

    def test_installs_metadata_yaml_when_present(self) -> None:
        """Copy metadata into the stable Vision-Hub model directory."""

        with tempfile.TemporaryDirectory() as export_temp, tempfile.TemporaryDirectory() as output_temp:
            export_dir = Path(export_temp)
            output_dir = Path(output_temp)
            param_path = export_dir / "model.ncnn.param"
            bin_path = export_dir / "model.ncnn.bin"
            metadata_path = export_dir / "metadata.yaml"
            param_path.write_text("graph", encoding="utf-8")
            bin_path.write_bytes(b"weights")
            metadata_path.write_text("task: detect\n", encoding="utf-8")

            installed = _install_ncnn_files(
                _resolve_ncnn_files(export_dir),
                output_dir,
                force=False,
            )

            self.assertEqual(installed.metadata_path, output_dir / "metadata.yaml")
            self.assertEqual((output_dir / "metadata.yaml").read_text(encoding="utf-8"), "task: detect\n")

    def test_temporary_working_directory_is_removed(self) -> None:
        """Create export files in a temporary directory and restore cwd."""

        original_cwd = Path.cwd()
        with _temporary_working_directory() as temp_dir:
            self.assertEqual(Path.cwd(), temp_dir)
            (temp_dir / "yolo11n_ncnn_model").mkdir()

        self.assertEqual(Path.cwd(), original_cwd)
        self.assertFalse(temp_dir.exists())


if __name__ == "__main__":
    unittest.main()
