import tempfile
import unittest
from pathlib import Path

import numpy as np

from vision_hub.inference.ncnn_yolo import (
    Detection,
    PersonDetectionResult,
    _as_yolo11_rows,
    _decode_person_detections,
    _nms,
    resolve_ncnn_model_files,
)


class NcnnYoloTest(unittest.TestCase):
    def test_resolves_ultralytics_ncnn_model_directory(self) -> None:
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
        output = np.zeros((144, 8400), dtype=np.float32)

        rows = _as_yolo11_rows(output)

        self.assertEqual(rows.shape, (8400, 144))

    def test_decodes_person_detection_only(self) -> None:
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

    def test_rejects_unexpected_yolo_row_count(self) -> None:
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
        detections = [
            Detection("person", 0, 0.9, 10, 10, 100, 100),
            Detection("person", 0, 0.8, 20, 20, 100, 100),
            Detection("person", 0, 0.7, 300, 300, 50, 50),
        ]

        picked = _nms(detections, threshold=0.45)

        self.assertEqual([detection.score for detection in picked], [0.9, 0.7])

    def test_person_detection_result_exposes_person_count(self) -> None:
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
