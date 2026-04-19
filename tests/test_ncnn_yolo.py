"""Tests for NCNN YOLO11 person detection helpers."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from vision_hub.inference.ncnn_yolo import (
    Detection,
    PersonDetectionResult,
    _as_yolo11_rows,
    _decode_exported_person_detections,
    _decode_person_detections,
    _nms,
    resolve_ncnn_model_files,
)


class NcnnYoloTest(unittest.TestCase):
    """Unit tests for NCNN YOLO model resolution and post-processing."""

    def test_resolves_ultralytics_ncnn_model_directory(self) -> None:
        """Resolve Ultralytics-style NCNN export directories."""

        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            param_path = model_dir / "model.ncnn.param"
            bin_path = model_dir / "model.ncnn.bin"
            param_path.write_text("", encoding="utf-8")
            bin_path.write_bytes(b"")

            files = resolve_ncnn_model_files(model_dir)

            self.assertEqual(files.param_path, param_path)
            self.assertEqual(files.bin_path, bin_path)

    def test_output_rows_accepts_transposed_yolo_shape(self) -> None:
        """Normalize transposed YOLO output into row-major shape."""

        output = np.zeros((144, 8400), dtype=np.float32)

        rows = _as_yolo11_rows(output)

        self.assertEqual(rows.shape, (8400, 144))

    def test_output_rows_accepts_ultralytics_exported_shape(self) -> None:
        """Normalize Ultralytics NCNN output into row-major shape."""

        output = np.zeros((84, 8400), dtype=np.float32)

        rows = _as_yolo11_rows(output)

        self.assertEqual(rows.shape, (8400, 84))

    def test_decodes_person_detection_only(self) -> None:
        """Decode only the COCO person class from YOLO rows."""

        rows = np.full((8400, 144), -12.0, dtype=np.float32)
        row = rows[0]
        row[0:64] = 0.0
        row[64] = 8.0

        detections = _decode_person_detections(
            rows=rows,
            padded_width=640,
            padded_height=640,
            image_width=640,
            image_height=640,
            scale=1.0,
            wpad=0,
            hpad=0,
            prob_threshold=0.25,
            nms_threshold=0.45,
        )

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].label, "person")
        self.assertGreater(detections[0].score, 0.99)

    def test_decodes_ultralytics_exported_person_detection(self) -> None:
        """Decode person detections from Ultralytics NCNN exported rows."""

        rows = np.zeros((8400, 84), dtype=np.float32)
        rows[0, 0:4] = [100.0, 120.0, 40.0, 60.0]
        rows[0, 4] = 0.91

        detections = _decode_exported_person_detections(
            rows=rows,
            padded_width=640,
            padded_height=640,
            image_width=640,
            image_height=640,
            scale=1.0,
            wpad=0,
            hpad=0,
            prob_threshold=0.25,
            nms_threshold=0.45,
        )

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].label, "person")
        self.assertAlmostEqual(detections[0].score, 0.91)
        self.assertAlmostEqual(detections[0].x, 80.0)
        self.assertAlmostEqual(detections[0].y, 90.0)
        self.assertAlmostEqual(detections[0].width, 40.0)
        self.assertAlmostEqual(detections[0].height, 60.0)

    def test_rejects_unexpected_yolo_row_count(self) -> None:
        """Reject YOLO outputs whose row count does not match input geometry."""

        rows = np.zeros((8399, 144), dtype=np.float32)

        with self.assertRaises(ValueError):
            _decode_person_detections(
                rows=rows,
                padded_width=640,
                padded_height=640,
                image_width=640,
                image_height=640,
                scale=1.0,
                wpad=0,
                hpad=0,
                prob_threshold=0.25,
                nms_threshold=0.45,
            )

    def test_nms_keeps_best_overlapping_detection(self) -> None:
        """Keep the strongest overlapping detection after NMS."""

        detections = [
            Detection("person", 0, 0.9, 10, 10, 100, 100),
            Detection("person", 0, 0.8, 20, 20, 100, 100),
            Detection("person", 0, 0.7, 300, 300, 50, 50),
        ]

        picked = _nms(detections, threshold=0.45)

        self.assertEqual([detection.score for detection in picked], [0.9, 0.7])

    def test_person_detection_result_exposes_person_count(self) -> None:
        """Serialize the person count in detection results."""

        result = PersonDetectionResult(
            person_detected=True,
            person_count=2,
            best_score=0.9,
            detections=(
                Detection("person", 0, 0.9, 10, 10, 100, 100),
                Detection("person", 0, 0.7, 300, 300, 50, 50),
            ),
        )

        self.assertEqual(result.to_dict()["person_count"], 2)


if __name__ == "__main__":
    unittest.main()
