"""NCNN-backed YOLO11 person detector."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ncnn
import numpy as np


PERSON_CLASS_ID = 0
COCO_PERSON_LABEL = "person"
YOLO11_STRIDES = (8, 16, 32)
YOLO11_REG_MAX = 16
YOLO11_COCO_CLASS_COUNT = 80


@dataclass(frozen=True)
class NcnnModelFiles:
    """Resolved NCNN model file pair.

    Attributes:
        param_path: Path to the NCNN `.param` graph file.
        bin_path: Path to the NCNN `.bin` weights file.
    """

    param_path: Path
    bin_path: Path


@dataclass(frozen=True)
class Detection:
    """One object detection in original image coordinates.

    Attributes:
        label: Human-readable class label.
        class_id: Numeric class id from the source model.
        score: Confidence score after post-processing.
        x: Left coordinate in original image pixels.
        y: Top coordinate in original image pixels.
        width: Box width in original image pixels.
        height: Box height in original image pixels.
    """

    label: str
    class_id: int
    score: float
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, float | int | str]:
        """Convert the detection into a JSON-serializable dictionary.

        Returns:
            Detection fields as primitive Python values.
        """

        return {
            "label": self.label,
            "class_id": self.class_id,
            "score": self.score,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class PersonDetectionResult:
    """Result returned by the person detector.

    Attributes:
        person_detected: Whether at least one person remains after NMS.
        person_count: Number of person detections after NMS.
        best_score: Highest person score, or `None` when no person is found.
        detections: Final person detections in descending score order.
    """

    person_detected: bool
    person_count: int
    best_score: float | None
    detections: tuple[Detection, ...]

    def to_dict(self) -> dict[str, Any]:
        """Convert the result into a JSON-serializable dictionary.

        Returns:
            Result fields as primitive Python values and nested dictionaries.
        """

        return {
            "person_detected": self.person_detected,
            "person_count": self.person_count,
            "best_score": self.best_score,
            "detections": [detection.to_dict() for detection in self.detections],
        }


class NcnnYolo11PersonDetector:
    """Pure NCNN YOLO11 detector filtered to the COCO person class."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        target_size: int = 640,
        prob_threshold: float = 0.25,
        nms_threshold: float = 0.45,
        num_threads: int = 4,
        use_vulkan: bool = False,
        input_name: str = "in0",
        output_name: str = "out0",
    ) -> None:
        """Load a NCNN YOLO11 model for person detection.

        Args:
            model_path: Directory containing NCNN files or direct `.param` path.
            target_size: Long-side inference size before stride padding.
            prob_threshold: Minimum person confidence to keep a proposal.
            nms_threshold: IoU threshold for non-maximum suppression.
            num_threads: Number of NCNN CPU threads.
            use_vulkan: Whether to enable NCNN Vulkan compute.
            input_name: NCNN input blob name.
            output_name: NCNN output blob name.

        Raises:
            ValueError: If numeric configuration values are invalid.
            RuntimeError: If NCNN cannot load the model files.
        """

        if target_size <= 0:
            raise ValueError("target_size must be > 0")
        if not 0.0 <= prob_threshold <= 1.0:
            raise ValueError("prob_threshold must be between 0 and 1")
        if not 0.0 <= nms_threshold <= 1.0:
            raise ValueError("nms_threshold must be between 0 and 1")
        if num_threads <= 0:
            raise ValueError("num_threads must be > 0")

        self.model_files = resolve_ncnn_model_files(model_path)
        self.target_size = target_size
        self.prob_threshold = prob_threshold
        self.nms_threshold = nms_threshold
        self.num_threads = num_threads
        self.use_vulkan = use_vulkan
        self.input_name = input_name
        self.output_name = output_name

        self._net = self._load_net()

    def detect_path(self, image_path: str | Path) -> PersonDetectionResult:
        """Run person detection on an image file.

        Args:
            image_path: Path to an image readable by OpenCV.

        Returns:
            Person detection result.

        Raises:
            ValueError: If the image cannot be read.
            RuntimeError: If NCNN inference fails.
        """

        import cv2

        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"could not read image: {image_path}")
        return self.detect_bgr(image)

    def detect_bgr(self, image: np.ndarray) -> PersonDetectionResult:
        """Run person detection on an in-memory BGR image.

        Args:
            image: BGR `uint8` image with shape `H x W x 3`.

        Returns:
            Person detection result.

        Raises:
            ValueError: If the image shape is not supported or model output has
                an unexpected shape.
            RuntimeError: If NCNN inference fails.
        """

        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("image must be a BGR uint8 array with shape HxWx3")

        mat_in_pad, scale, wpad, hpad = self._preprocess(image)
        with self._net.create_extractor() as extractor:
            extractor.input(self.input_name, mat_in_pad)
            ret, output = extractor.extract(self.output_name)

        if ret != 0:
            raise RuntimeError(f"NCNN extract failed for output {self.output_name!r}: {ret}")

        rows = _as_yolo11_rows(np.array(output))
        detections = _decode_person_detections(
            rows=rows,
            padded_width=mat_in_pad.w,
            padded_height=mat_in_pad.h,
            image_width=image.shape[1],
            image_height=image.shape[0],
            scale=scale,
            wpad=wpad,
            hpad=hpad,
            prob_threshold=self.prob_threshold,
            nms_threshold=self.nms_threshold,
        )

        best_score = detections[0].score if detections else None
        return PersonDetectionResult(
            person_detected=bool(detections),
            person_count=len(detections),
            best_score=best_score,
            detections=tuple(detections),
        )

    def _load_net(self):
        """Load NCNN graph and weights into a `ncnn.Net` instance.

        Returns:
            Loaded NCNN network.

        Raises:
            RuntimeError: If NCNN fails to load either file.
        """

        net = ncnn.Net()
        net.opt.use_vulkan_compute = self.use_vulkan
        net.opt.num_threads = self.num_threads

        param_ret = net.load_param(str(self.model_files.param_path))
        if param_ret != 0:
            raise RuntimeError(f"NCNN load_param failed: {self.model_files.param_path}")

        model_ret = net.load_model(str(self.model_files.bin_path))
        if model_ret != 0:
            raise RuntimeError(f"NCNN load_model failed: {self.model_files.bin_path}")

        return net

    def _preprocess(self, image: np.ndarray):
        """Resize, pad, convert, and normalize a BGR image for YOLO11.

        Args:
            image: BGR image with shape `H x W x 3`.

        Returns:
            Tuple containing the padded NCNN input matrix, resize scale,
            horizontal padding, and vertical padding.
        """

        image_height, image_width = image.shape[:2]

        width = image_width
        height = image_height
        scale = 1.0
        if width > height:
            scale = self.target_size / width
            width = self.target_size
            height = int(height * scale)
        else:
            scale = self.target_size / height
            height = self.target_size
            width = int(width * scale)

        mat_in = ncnn.Mat.from_pixels_resize(
            image,
            ncnn.Mat.PixelType.PIXEL_BGR2RGB,
            image_width,
            image_height,
            width,
            height,
        )

        max_stride = max(YOLO11_STRIDES)
        wpad = (width + max_stride - 1) // max_stride * max_stride - width
        hpad = (height + max_stride - 1) // max_stride * max_stride - height

        mat_in_pad = ncnn.copy_make_border(
            mat_in,
            hpad // 2,
            hpad - hpad // 2,
            wpad // 2,
            wpad - wpad // 2,
            ncnn.BorderType.BORDER_CONSTANT,
            114.0,
        )
        mat_in_pad.substract_mean_normalize([], [1 / 255.0, 1 / 255.0, 1 / 255.0])
        return mat_in_pad, scale, wpad, hpad


def resolve_ncnn_model_files(model_path: str | Path) -> NcnnModelFiles:
    """Resolve a model directory or `.param` path into NCNN model files.

    Args:
        model_path: Directory containing NCNN files or direct `.param` path.

    Returns:
        Resolved `.param` and `.bin` file paths.

    Raises:
        ValueError: If the path does not identify a valid NCNN model pair.
    """

    path = Path(model_path)
    if path.is_dir():
        return _resolve_model_dir(path)
    if path.suffix == ".param":
        return _pair_from_param(path)
    raise ValueError("model_path must be a NCNN model directory or .param file")


def _resolve_model_dir(path: Path) -> NcnnModelFiles:
    """Find NCNN model files inside a directory.

    Args:
        path: Directory to inspect.

    Returns:
        Resolved `.param` and `.bin` file paths.

    Raises:
        ValueError: If no unambiguous model file pair can be found.
    """

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
        raise ValueError(f"expected exactly one .param file in {path}")
    return _pair_from_param(param_files[0])


def _pair_from_param(param_path: Path) -> NcnnModelFiles:
    """Resolve a `.param` file and its sibling `.bin` file.

    Args:
        param_path: Path to the NCNN `.param` file.

    Returns:
        Resolved model file pair.

    Raises:
        ValueError: If either file does not exist.
    """

    bin_path = param_path.with_suffix(".bin")
    if not param_path.exists():
        raise ValueError(f"NCNN param file does not exist: {param_path}")
    if not bin_path.exists():
        raise ValueError(f"NCNN bin file does not exist: {bin_path}")
    return NcnnModelFiles(param_path=param_path, bin_path=bin_path)


def _decode_person_detections(
    *,
    rows: np.ndarray,
    padded_width: int,
    padded_height: int,
    image_width: int,
    image_height: int,
    scale: float,
    wpad: int,
    hpad: int,
    prob_threshold: float,
    nms_threshold: float,
) -> list[Detection]:
    """Decode YOLO11 output rows into person detections.

    Args:
        rows: YOLO11 output rows with box distributions and class logits.
        padded_width: Width of the padded model input.
        padded_height: Height of the padded model input.
        image_width: Original image width.
        image_height: Original image height.
        scale: Resize scale applied before padding.
        wpad: Horizontal padding added after resize.
        hpad: Vertical padding added after resize.
        prob_threshold: Minimum person score to keep a proposal.
        nms_threshold: IoU threshold for non-maximum suppression.

    Returns:
        Final person detections after thresholding and NMS.

    Raises:
        ValueError: If the row count does not match the padded input geometry.
    """

    proposals: list[Detection] = []
    row_offset = 0
    expected_rows = sum((padded_width // stride) * (padded_height // stride) for stride in YOLO11_STRIDES)
    if rows.shape[0] != expected_rows:
        raise ValueError(f"expected {expected_rows} YOLO11 rows for padded input, got {rows.shape[0]}")

    for stride in YOLO11_STRIDES:
        grid_x = padded_width // stride
        grid_y = padded_height // stride
        grid_count = grid_x * grid_y
        stride_rows = rows[row_offset : row_offset + grid_count]
        row_offset += grid_count

        for index, row in enumerate(stride_rows):
            person_score = float(_sigmoid(row[YOLO11_REG_MAX * 4 + PERSON_CLASS_ID]))
            if person_score < prob_threshold:
                continue

            grid_y_index = index // grid_x
            grid_x_index = index % grid_x
            distances = _decode_ltrb(row[: YOLO11_REG_MAX * 4]) * stride

            center_x = (grid_x_index + 0.5) * stride
            center_y = (grid_y_index + 0.5) * stride
            padded_x0 = center_x - distances[0]
            padded_y0 = center_y - distances[1]
            padded_x1 = center_x + distances[2]
            padded_y1 = center_y + distances[3]

            x0 = _clip((padded_x0 - (wpad / 2)) / scale, 0.0, image_width - 1.0)
            y0 = _clip((padded_y0 - (hpad / 2)) / scale, 0.0, image_height - 1.0)
            x1 = _clip((padded_x1 - (wpad / 2)) / scale, 0.0, image_width - 1.0)
            y1 = _clip((padded_y1 - (hpad / 2)) / scale, 0.0, image_height - 1.0)

            if x1 <= x0 or y1 <= y0:
                continue

            proposals.append(
                Detection(
                    label=COCO_PERSON_LABEL,
                    class_id=PERSON_CLASS_ID,
                    score=person_score,
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                )
            )

    proposals.sort(key=lambda detection: detection.score, reverse=True)
    return _nms(proposals, nms_threshold)


def _as_yolo11_rows(output: np.ndarray) -> np.ndarray:
    """Normalize a NCNN YOLO11 output tensor to row-major shape.

    Args:
        output: Raw NCNN output tensor converted to NumPy.

    Returns:
        Two-dimensional array shaped as `rows x 144`.

    Raises:
        ValueError: If the output tensor shape is unsupported.
    """

    squeezed = np.squeeze(output).astype(np.float32, copy=False)
    if squeezed.ndim != 2:
        raise ValueError(f"expected YOLO11 output to be 2D, got shape {output.shape}")

    expected_width = YOLO11_REG_MAX * 4 + YOLO11_COCO_CLASS_COUNT
    if squeezed.shape[1] == expected_width:
        return squeezed
    if squeezed.shape[0] == expected_width:
        return squeezed.T

    raise ValueError(f"expected one YOLO11 output dimension to be {expected_width}, got {output.shape}")


def _decode_ltrb(raw: np.ndarray) -> np.ndarray:
    """Decode YOLO11 DFL side distributions into LTRB distances.

    Args:
        raw: Flattened box distribution values for one YOLO row.

    Returns:
        Array of decoded left, top, right, and bottom distances.
    """

    distances = raw.reshape(4, YOLO11_REG_MAX)
    probabilities = _softmax(distances, axis=1)
    bins = np.arange(YOLO11_REG_MAX, dtype=np.float32)
    return probabilities @ bins


def _nms(detections: list[Detection], threshold: float) -> list[Detection]:
    """Apply non-maximum suppression to sorted detections.

    Args:
        detections: Candidate detections sorted by descending score.
        threshold: IoU threshold above which a detection is suppressed.

    Returns:
        Detections kept after suppression.
    """

    picked: list[Detection] = []
    for detection in detections:
        if all(_iou(detection, existing) <= threshold for existing in picked):
            picked.append(detection)
    return picked


def _iou(a: Detection, b: Detection) -> float:
    """Compute intersection-over-union for two detections.

    Args:
        a: First detection.
        b: Second detection.

    Returns:
        IoU value in the range `0.0` to `1.0`.
    """

    ax1, ay1, ax2, ay2 = a.x, a.y, a.x + a.width, a.y + a.height
    bx1, by1, bx2, by2 = b.x, b.y, b.x + b.width, b.y + b.height

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area == 0.0:
        return 0.0

    union_area = a.width * a.height + b.width * b.height - inter_area
    return inter_area / union_area if union_area > 0.0 else 0.0


def _sigmoid(value: float | np.ndarray) -> float | np.ndarray:
    """Apply the logistic sigmoid function.

    Args:
        value: Scalar or NumPy array of logits.

    Returns:
        Scalar or array mapped to the range `0.0` to `1.0`.
    """

    return 1.0 / (1.0 + np.exp(-value))


def _softmax(values: np.ndarray, axis: int) -> np.ndarray:
    """Apply numerically stable softmax along one axis.

    Args:
        values: Input logits.
        axis: Axis over which probabilities are normalized.

    Returns:
        Probability array with the same shape as `values`.
    """

    shifted = values - np.max(values, axis=axis, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=axis, keepdims=True)


def _clip(value: float, minimum: float, maximum: float) -> float:
    """Clamp a scalar value to an inclusive range.

    Args:
        value: Value to clamp.
        minimum: Inclusive lower bound.
        maximum: Inclusive upper bound.

    Returns:
        Clamped value.
    """

    return max(min(value, maximum), minimum)
