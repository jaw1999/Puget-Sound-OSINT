"""
Vessel Detection using YOLOv8.

Detects boats and ships in camera feed images using pre-trained
or custom-trained YOLOv8 models.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from enum import Enum
import numpy as np

logger = logging.getLogger(__name__)


class VesselType(Enum):
    """Detected vessel types."""
    FERRY = "ferry"
    BOAT = "boat"
    SHIP = "ship"
    SAILBOAT = "sailboat"
    UNKNOWN = "unknown"


class DetectionStatus(Enum):
    """Detection confidence levels."""
    HIGH = "high"      # >= 0.7
    MEDIUM = "medium"  # >= 0.5
    LOW = "low"        # >= threshold


@dataclass
class BoundingBox:
    """Bounding box coordinates (normalized 0-1 or pixel values)."""
    x1: float  # Top-left x
    y1: float  # Top-left y
    x2: float  # Bottom-right x
    y2: float  # Bottom-right y

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def area(self) -> float:
        return self.width * self.height

    def to_dict(self) -> Dict:
        return {
            "x1": self.x1, "y1": self.y1,
            "x2": self.x2, "y2": self.y2,
            "width": self.width, "height": self.height,
            "center": self.center
        }


@dataclass
class Detection:
    """A single vessel detection result."""
    detection_id: str
    vessel_type: VesselType
    confidence: float
    bbox: BoundingBox
    class_name: str  # Raw YOLO class name
    class_id: int    # YOLO class ID
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Optional attributes (set by classifier/tracker)
    vessel_name: Optional[str] = None
    vessel_id: Optional[int] = None
    heading_estimate: Optional[float] = None

    @property
    def status(self) -> DetectionStatus:
        if self.confidence >= 0.7:
            return DetectionStatus.HIGH
        elif self.confidence >= 0.5:
            return DetectionStatus.MEDIUM
        return DetectionStatus.LOW

    def to_dict(self) -> Dict:
        return {
            "detection_id": self.detection_id,
            "vessel_type": self.vessel_type.value,
            "confidence": round(self.confidence, 3),
            "status": self.status.value,
            "bbox": self.bbox.to_dict(),
            "class_name": self.class_name,
            "timestamp": self.timestamp.isoformat(),
            "vessel_name": self.vessel_name,
            "vessel_id": self.vessel_id,
            "heading_estimate": self.heading_estimate
        }


@dataclass
class DetectionResult:
    """Results from processing a single frame."""
    camera_id: str
    frame_timestamp: datetime
    detections: List[Detection]
    processing_time_ms: float
    frame_shape: Tuple[int, int]  # (height, width)

    @property
    def detection_count(self) -> int:
        return len(self.detections)

    @property
    def has_detections(self) -> bool:
        return len(self.detections) > 0

    def to_dict(self) -> Dict:
        return {
            "camera_id": self.camera_id,
            "timestamp": self.frame_timestamp.isoformat(),
            "detection_count": self.detection_count,
            "detections": [d.to_dict() for d in self.detections],
            "processing_time_ms": round(self.processing_time_ms, 2),
            "frame_shape": self.frame_shape
        }


# YOLO COCO class IDs for boats/vessels
VESSEL_CLASS_IDS = {
    8: ("boat", VesselType.BOAT),      # COCO class 8 = boat
}

# Additional maritime classes if using custom model
MARITIME_CLASS_MAP = {
    "boat": VesselType.BOAT,
    "ship": VesselType.SHIP,
    "ferry": VesselType.FERRY,
    "sailboat": VesselType.SAILBOAT,
    "vessel": VesselType.BOAT,
    "watercraft": VesselType.BOAT,
}


class VesselDetector:
    """
    YOLOv8-based vessel detector for camera feeds.

    Usage:
        detector = VesselDetector(model_path="yolov8n.pt")

        # Detect in image
        result = detector.detect(image, camera_id="seattle")

        # Get annotated image
        annotated = detector.annotate(image, result.detections)
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        device: str = "cpu",
        vessel_classes_only: bool = True,
        img_size: int = 640
    ):
        """
        Initialize the vessel detector.

        Args:
            model_path: Path to YOLO model weights (.pt file)
                       Use "yolov8n.pt" for nano, "yolov8s.pt" for small, etc.
            confidence_threshold: Minimum confidence for detections
            iou_threshold: IOU threshold for NMS
            device: Device to run inference ("cpu", "cuda:0", "mps")
            vessel_classes_only: Only detect boats/vessels (filter other classes)
            img_size: Input image size for model
        """
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.device = device
        self.vessel_classes_only = vessel_classes_only
        self.img_size = img_size

        self._model = None
        self._detection_counter = 0

    def _load_model(self):
        """Lazy-load the YOLO model."""
        if self._model is None:
            try:
                from ultralytics import YOLO
                logger.info(f"Loading YOLO model: {self.model_path}")
                self._model = YOLO(self.model_path)
                logger.info(f"Model loaded on device: {self.device}")
            except Exception as e:
                logger.error(f"Failed to load YOLO model: {e}")
                raise
        return self._model

    def _generate_detection_id(self) -> str:
        """Generate unique detection ID."""
        self._detection_counter += 1
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"det_{ts}_{self._detection_counter:06d}"

    def detect(
        self,
        image: Union[np.ndarray, str, Path],
        camera_id: str = "unknown"
    ) -> DetectionResult:
        """
        Run vessel detection on an image.

        Args:
            image: Input image as numpy array (BGR), or path to image file
            camera_id: Identifier for the source camera

        Returns:
            DetectionResult with all detections
        """
        import time
        start_time = time.perf_counter()

        model = self._load_model()
        frame_time = datetime.now(timezone.utc)

        # Run inference
        results = model.predict(
            source=image,
            conf=self.confidence_threshold,
            iou=self.iou_threshold,
            device=self.device,
            imgsz=self.img_size,
            verbose=False,
            classes=list(VESSEL_CLASS_IDS.keys()) if self.vessel_classes_only else None
        )

        detections = []
        frame_shape = (0, 0)

        for result in results:
            if result.orig_shape:
                frame_shape = result.orig_shape[:2]

            if result.boxes is None:
                continue

            for box in result.boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                coords = box.xyxy[0].cpu().numpy()

                # Get class name and vessel type
                class_name = model.names.get(class_id, "unknown")
                vessel_type = self._get_vessel_type(class_id, class_name)

                # Skip non-vessel detections if filter enabled
                if self.vessel_classes_only and vessel_type == VesselType.UNKNOWN:
                    continue

                bbox = BoundingBox(
                    x1=float(coords[0]),
                    y1=float(coords[1]),
                    x2=float(coords[2]),
                    y2=float(coords[3])
                )

                detection = Detection(
                    detection_id=self._generate_detection_id(),
                    vessel_type=vessel_type,
                    confidence=confidence,
                    bbox=bbox,
                    class_name=class_name,
                    class_id=class_id,
                    timestamp=frame_time
                )
                detections.append(detection)

        processing_time = (time.perf_counter() - start_time) * 1000

        return DetectionResult(
            camera_id=camera_id,
            frame_timestamp=frame_time,
            detections=detections,
            processing_time_ms=processing_time,
            frame_shape=frame_shape
        )

    def _get_vessel_type(self, class_id: int, class_name: str) -> VesselType:
        """Determine vessel type from YOLO class."""
        # Check predefined COCO vessel classes
        if class_id in VESSEL_CLASS_IDS:
            return VESSEL_CLASS_IDS[class_id][1]

        # Check class name against maritime vocabulary
        class_lower = class_name.lower()
        for keyword, vessel_type in MARITIME_CLASS_MAP.items():
            if keyword in class_lower:
                return vessel_type

        return VesselType.UNKNOWN

    def annotate(
        self,
        image: np.ndarray,
        detections: List[Detection],
        show_labels: bool = True,
        show_confidence: bool = True,
        box_color: Tuple[int, int, int] = (0, 255, 0),
        box_thickness: int = 2,
        font_scale: float = 0.6
    ) -> np.ndarray:
        """
        Draw detection boxes on image.

        Args:
            image: Input image (BGR)
            detections: List of Detection objects
            show_labels: Show class labels
            show_confidence: Show confidence scores
            box_color: BGR color for boxes
            box_thickness: Line thickness
            font_scale: Text scale

        Returns:
            Annotated image copy
        """
        import cv2

        annotated = image.copy()

        for det in detections:
            # Draw bounding box
            x1, y1 = int(det.bbox.x1), int(det.bbox.y1)
            x2, y2 = int(det.bbox.x2), int(det.bbox.y2)

            # Color based on confidence
            if det.confidence >= 0.7:
                color = (0, 255, 0)  # Green for high confidence
            elif det.confidence >= 0.5:
                color = (0, 255, 255)  # Yellow for medium
            else:
                color = (0, 165, 255)  # Orange for low

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, box_thickness)

            if show_labels:
                label_parts = []
                if det.vessel_name:
                    label_parts.append(det.vessel_name)
                else:
                    label_parts.append(det.vessel_type.value.upper())

                if show_confidence:
                    label_parts.append(f"{det.confidence:.0%}")

                label = " ".join(label_parts)

                # Background for text
                (text_w, text_h), _ = cv2.getTextSize(
                    label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 2
                )
                cv2.rectangle(
                    annotated,
                    (x1, y1 - text_h - 10),
                    (x1 + text_w + 10, y1),
                    color, -1
                )
                cv2.putText(
                    annotated, label,
                    (x1 + 5, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale, (0, 0, 0), 2
                )

        return annotated

    def detect_and_annotate(
        self,
        image: Union[np.ndarray, str, Path],
        camera_id: str = "unknown"
    ) -> Tuple[DetectionResult, np.ndarray]:
        """
        Detect vessels and return annotated image.

        Args:
            image: Input image
            camera_id: Camera identifier

        Returns:
            Tuple of (DetectionResult, annotated_image)
        """
        # Load image if path
        if isinstance(image, (str, Path)):
            import cv2
            image = cv2.imread(str(image))
            if image is None:
                raise ValueError(f"Could not load image: {image}")

        result = self.detect(image, camera_id)
        annotated = self.annotate(image, result.detections)

        return result, annotated


class DetectionBuffer:
    """
    Buffer for tracking detections across frames.

    Helps with:
    - Smoothing detections over time
    - Tracking objects across frames
    - Reducing false positives via temporal filtering
    """

    def __init__(self, max_frames: int = 30, min_hits: int = 3):
        """
        Args:
            max_frames: Maximum frames to buffer
            min_hits: Minimum detections needed to confirm object
        """
        self.max_frames = max_frames
        self.min_hits = min_hits
        self._buffer: List[DetectionResult] = []

    def add(self, result: DetectionResult):
        """Add detection result to buffer."""
        self._buffer.append(result)
        if len(self._buffer) > self.max_frames:
            self._buffer.pop(0)

    def get_confirmed_detections(self) -> List[Detection]:
        """Get detections that appear consistently across frames."""
        # Simple implementation - can be enhanced with IoU tracking
        if len(self._buffer) < self.min_hits:
            return []

        # Return most recent detections if consistent activity
        recent = self._buffer[-1]
        return recent.detections if recent.has_detections else []

    def clear(self):
        """Clear the buffer."""
        self._buffer.clear()
