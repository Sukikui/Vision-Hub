"""Local image inference backends."""

from vision_hub.inference.ncnn_yolo import (
    Detection,
    NcnnModelFiles,
    NcnnYolo11PersonDetector,
    PersonDetectionResult,
    resolve_ncnn_model_files,
)

__all__ = [
    "Detection",
    "NcnnModelFiles",
    "NcnnYolo11PersonDetector",
    "PersonDetectionResult",
    "resolve_ncnn_model_files",
]
