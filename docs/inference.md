# Inference

## Overview

Vision-Hub performs image inference locally on the Raspberry Pi. ESP32-P4 nodes send captures to the hub; the hub runs a person detector and returns a compact result for the decision layer.

This module does not perform face recognition. It detects the COCO `person` class only.

## Runtime Architecture

| Component | Responsibility |
| --- | --- |
| ESP32-P4 node | captures and sends images |
| Vision-Hub image layer | reconstructs and stores the received image |
| Inference module | runs YOLO11 through NCNN |
| Decision layer | consumes `PersonDetectionResult` |

The inference module is deliberately independent from MQTT and storage. It receives either an image path or a BGR NumPy array and returns a typed Python result.

## Technical Stack

| Layer | Choice | Reason |
| --- | --- | --- |
| Runtime language | Python 3.13+ | same runtime as the hub service |
| Inference runtime | NCNN | lightweight ARM-friendly runtime |
| Python package | `ncnn` | direct NCNN bindings |
| Model | YOLO11n COCO | small detector suitable as the default Raspberry Pi model |
| Target class | COCO `person` | class id `0` |
| Tensor handling | NumPy | output decoding and post-processing |
| File image loading | OpenCV `cv2` | only used by `detect_path()` |

The Raspberry Pi runtime does not depend on `torch` or `ultralytics`. Those tools can be used outside the hub to export the model, but inference itself loads NCNN files directly.

## Model Storage

Model files are runtime assets, not Python source files.

Standard deployment path:

```text
/opt/vision-hub/models/person-detector/yolo11n-ncnn/
  model.ncnn.param
  model.ncnn.bin
```

Local development path:

```text
models/person-detector/yolo11n-ncnn/
  model.ncnn.param
  model.ncnn.bin
```

Docker uses the same logical contract: the host model directory is mounted read-only into the Vision-Hub container.

Example:

```text
/opt/vision-hub/models:/opt/vision-hub/models:ro
```

## Model Files

| File | Content | Loaded by |
| --- | --- | --- |
| `model.ncnn.param` | NCNN graph definition | `ncnn.Net.load_param()` |
| `model.ncnn.bin` | model weights | `ncnn.Net.load_model()` |

The `ncnn` Python dependency provides the runtime, not the YOLO weights. The weights must be provisioned as model artifacts before the service starts.

Supported file layouts:

| Input passed to detector | Accepted files |
| --- | --- |
| model directory | `model.ncnn.param` + `model.ncnn.bin` |
| model directory | `yolo11n.ncnn.param` + `yolo11n.ncnn.bin` |
| model directory | exactly one `*.param` file with a matching `*.bin` |
| direct `.param` path | matching `.bin` beside it |

## Model Import Flow

In Vision-Hub, "importing the model" means loading NCNN artifacts from disk. The `.param` and `.bin` files are not imported with Python's `import` mechanism.

Python imports the detector class:

```python
from vision_hub.inference import NcnnYolo11PersonDetector
```

NCNN loads the model files:

```python
detector = NcnnYolo11PersonDetector(
    model_path="/opt/vision-hub/models/person-detector/yolo11n-ncnn",
    target_size=640,
    prob_threshold=0.25,
    nms_threshold=0.45,
    num_threads=4,
)
```

Internal loading sequence:

| Step | Call |
| --- | --- |
| 1 | resolve the `.param` path |
| 2 | resolve the matching `.bin` path |
| 3 | create `ncnn.Net()` |
| 4 | call `net.load_param(...)` |
| 5 | call `net.load_model(...)` |
| 6 | keep the loaded `Net` inside the detector instance |

The detector is created once by the long-running service, then reused for captures. It should not be recreated for every image.

## Detector API

| Item | Value |
| --- | --- |
| class | `NcnnYolo11PersonDetector` |
| module | `vision_hub.inference.ncnn_yolo` |
| file path API | `detect_path(image_path)` |
| in-memory API | `detect_bgr(image)` |
| input array format | `uint8` BGR, shape `H x W x 3` |
| output type | `PersonDetectionResult` |

Configuration:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `model_path` | required | NCNN model directory or `.param` file |
| `target_size` | `640` | YOLO input size before stride padding |
| `prob_threshold` | `0.25` | minimum person confidence |
| `nms_threshold` | `0.45` | IoU threshold used by NMS |
| `num_threads` | `4` | NCNN CPU thread count |
| `use_vulkan` | `False` | Vulkan execution flag |
| `input_name` | `in0` | NCNN input blob name |
| `output_name` | `out0` | NCNN output blob name |

## Image Preprocessing

| Step | Operation |
| --- | --- |
| 1 | receive BGR `uint8` image |
| 2 | resize while preserving aspect ratio |
| 3 | convert BGR to RGB through `ncnn.Mat.from_pixels_resize(...)` |
| 4 | pad to a multiple of the maximum YOLO stride |
| 5 | use padding value `114` |
| 6 | normalize channels with scale `1 / 255` |

The detector keeps the resize scale and padding values so decoded boxes can be projected back into original image coordinates.

## YOLO11 Output Contract

The decoder expects YOLO11 COCO output rows with this layout:

| Segment | Size | Meaning |
| --- | --- | --- |
| box distribution | `64` | 4 sides x 16 distance bins |
| class logits | `80` | one score per COCO class |
| total | `144` | `64 + 80` |

For a padded `640 x 640` input:

| Stride | Grid | Rows |
| --- | --- | --- |
| `8` | `80 x 80` | `6400` |
| `16` | `40 x 40` | `1600` |
| `32` | `20 x 20` | `400` |
| total | | `8400` |

Accepted tensor layouts:

| Layout | Handling |
| --- | --- |
| `8400 x 144` | used directly |
| `144 x 8400` | transposed before decoding |

## Box Decoding

YOLO11 represents each box side as a distribution over `16` bins.

| Side | Raw values |
| --- | --- |
| left | 16 logits |
| top | 16 logits |
| right | 16 logits |
| bottom | 16 logits |

The decoder applies `softmax` independently to the 16 logits of each side:

```text
probabilities = softmax(side_logits)
distance = sum(probabilities[i] * i)
```

That distance is then multiplied by the stride of the grid level. The four decoded distances are interpreted as `left`, `top`, `right`, and `bottom` offsets from the grid cell center.

`softmax` is only used for box distance decoding. It is not used to select the object class.

## Person Score Decoding

The detector keeps only the COCO `person` class:

```text
PERSON_CLASS_ID = 0
person_logit = row[64 + PERSON_CLASS_ID]
person_score = sigmoid(person_logit)
```

This is class filtering, not a smaller inference pass. The full YOLO model still runs; Vision-Hub only discards non-person classes during post-processing.

| Function | Used for |
| --- | --- |
| `softmax` | distribution-to-distance conversion for boxes |
| `sigmoid` | person class confidence |

## NMS

Non-Maximum Suppression removes duplicate boxes for the same visible person.

| Step | Operation |
| --- | --- |
| 1 | sort person proposals by confidence |
| 2 | keep the highest-confidence proposal |
| 3 | compute IoU with already kept boxes |
| 4 | discard proposals whose IoU is above `nms_threshold` |
| 5 | return remaining detections |

`person_count` is the number of person detections after NMS.

## Result Format

`detect_bgr()` and `detect_path()` return `PersonDetectionResult`.

| Field | Type | Meaning |
| --- | --- | --- |
| `person_detected` | `bool` | true if at least one person remains after NMS |
| `person_count` | `int` | number of person detections after NMS |
| `best_score` | `float | None` | highest person confidence |
| `detections` | `tuple[Detection, ...]` | final person boxes |

Each `Detection` uses original image coordinates:

| Field | Type | Meaning |
| --- | --- | --- |
| `label` | `str` | `person` |
| `class_id` | `int` | `0` |
| `score` | `float` | person confidence |
| `x` | `float` | left pixel coordinate |
| `y` | `float` | top pixel coordinate |
| `width` | `float` | box width in pixels |
| `height` | `float` | box height in pixels |

Example:

```json
{
  "person_detected": true,
  "person_count": 2,
  "best_score": 0.91,
  "detections": [
    {
      "label": "person",
      "class_id": 0,
      "score": 0.91,
      "x": 120.0,
      "y": 44.0,
      "width": 180.0,
      "height": 430.0
    },
    {
      "label": "person",
      "class_id": 0,
      "score": 0.78,
      "x": 420.0,
      "y": 80.0,
      "width": 130.0,
      "height": 360.0
    }
  ]
}
```

## References

| Reference | Usage |
| --- | --- |
| <https://github.com/Tencent/ncnn/blob/master/examples/yolo11.cpp> | NCNN YOLO11 preprocessing and decoding reference |
| <https://github.com/ultralytics/ultralytics/blob/main/ultralytics/utils/loss.py> | YOLO class logits trained with binary cross-entropy |
