"""
Puget Sound OSINT Platform - Main Application

Coordinates:
- Camera feed polling (WSDOT terminals, third-party cams)
- Vessel detection and classification
- TACREP report generation
- ChatSurfer streaming integration
- Web dashboard API
"""

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import yaml

from .ingestion.feed_manager import FeedManager, FeedManagerConfig, CameraFeed
from .reporting.tacrep import TacrepGenerator, ConfidenceLevel
from .reporting.chatsurfer import ChatSurferClient, ChatSurferConfig
from .reporting.deconfliction import TacrepDeconfliction

logger = logging.getLogger(__name__)


class PugetSoundOSINT:
    """
    Main OSINT platform application.

    Orchestrates camera polling, detection, and reporting.
    """

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config = self._load_config(config_path)

        # Components
        self._feed_manager: Optional[FeedManager] = None
        self._chatsurfer: Optional[ChatSurferClient] = None
        self._detector = None  # Will be YOLOv8 detector
        self._deconfliction = TacrepDeconfliction(suppress_window_sec=120.0)

        # State
        self._running = False
        self._tai_mapping: Dict[str, str] = {}  # terminal_id -> TAI code

        # Load TAI mapping
        self._load_tai_mapping()

    @property
    def feed_manager(self) -> Optional[FeedManager]:
        return self._feed_manager

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load application configuration."""
        path = Path(config_path)
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f)
        return {}

    def _load_tai_mapping(self):
        """Load TAI code mappings from config."""
        tai_path = Path("config/tai_mapping.yaml")
        if tai_path.exists():
            with open(tai_path) as f:
                tai_config = yaml.safe_load(f)
                for tai_code, info in tai_config.get("tai_codes", {}).items():
                    terminal = info.get("terminal", "").lower().replace(" ", "")
                    self._tai_mapping[terminal] = tai_code
        logger.info(f"Loaded {len(self._tai_mapping)} TAI mappings")

    def initialize(self) -> bool:
        """Initialize all components."""
        logger.info("Initializing Puget Sound OSINT Platform...")

        # Feed Manager
        feed_config = FeedManagerConfig(
            cameras_config_path=self.config.get("cameras_config", "config/cameras.yaml"),
            storage_path=self.config.get("storage_path", "./captures"),
            save_all_frames=self.config.get("save_all_frames", True),
        )
        self._feed_manager = FeedManager(feed_config)
        self._feed_manager.set_detection_callback(self._on_frame_captured)

        # ChatSurfer client
        cs_config = self.config.get("chatsurfer", {})
        chatsurfer_config = ChatSurferConfig(
            enabled=cs_config.get("enabled", True),
            callsign=cs_config.get("callsign", "PR01"),
            mode=cs_config.get("mode", "stdout"),
            server_url=cs_config.get("server_url", "https://chatsurfer.nro.mil"),
            session=cs_config.get("session", ""),
            room=cs_config.get("room", ""),
            nickname=cs_config.get("nickname", "OSINT_Bot"),
            domain=cs_config.get("domain", "chatsurferxmppunclass"),
            classification=cs_config.get("classification", "UNCLASSIFIED//FOUO"),
            output_file=cs_config.get("output_file", "reports/tacreps.log"),
            image_base_url=cs_config.get("image_base_url", "http://localhost:8080/images/"),
            image_storage_path=self.config.get("storage_path", "./captures"),
            min_report_interval_sec=cs_config.get("min_report_interval_sec", 30.0),
        )
        self._chatsurfer = ChatSurferClient(chatsurfer_config)

        # Initialize YOLOv8 detector
        det_config = self.config.get("detector", {})
        if det_config.get("enabled", False):
            try:
                from .detection import VesselDetector
                self._detector = VesselDetector(
                    model_path=det_config.get("model_path", "yolov8n.pt"),
                    confidence_threshold=det_config.get("confidence_threshold", 0.25),
                    device=det_config.get("device", "cpu"),
                )
                logger.info("YOLOv8 detector initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize detector: {e}")
                self._detector = None
        else:
            logger.info("Detector disabled in config")

        logger.info("Initialization complete")
        return True

    def start(self) -> bool:
        """Start all components."""
        self._running = True

        # Start feed polling
        self._feed_manager.start()

        # Start ChatSurfer
        self._chatsurfer.start()

        # Send check-in
        self._chatsurfer.check_in()

        logger.info("Puget Sound OSINT Platform started")
        return True

    def stop(self):
        """Stop all components."""
        logger.info("Stopping Puget Sound OSINT Platform...")

        self._running = False

        # Send check-out
        if self._chatsurfer:
            self._chatsurfer.check_out()
            time.sleep(1)  # Allow message to send
            self._chatsurfer.stop()

        if self._feed_manager:
            self._feed_manager.stop()

        logger.info("Platform stopped")

    def _on_frame_captured(self, feed_id: str, frame: np.ndarray, feed: CameraFeed):
        """
        Callback when a camera frame is captured.

        This is where detection and reporting happens.
        """
        # Skip if no TAI code assigned to this feed
        tai_code = feed.tai_code
        if not tai_code:
            # Try to find TAI from terminal name
            for key, code in self._tai_mapping.items():
                if key in feed_id.lower() or key in feed.name.lower():
                    tai_code = code
                    break

        if not tai_code:
            return  # No TAI assignment, skip reporting

        # Run YOLOv8 detection if detector is available
        if self._detector:
            result = self._detector.detect(frame, camera_id=feed_id)
            detections = [
                {
                    "vessel_class": d.vessel_type.value,
                    "vessel_name": d.vessel_name,
                    "confidence": d.confidence,
                    "bbox": [d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2],
                }
                for d in result.detections
            ]
        else:
            detections = []

        if not detections:
            return

        cam_lat, cam_lon = feed.coordinates

        # Process each detection with deconfliction
        for detection in detections:
            vessel_key = f"VISUAL_{feed_id}"

            # Check deconfliction - skip if API already reported this vessel
            should_send, correlated_name, upgraded_conf = (
                self._deconfliction.should_report(
                    tai=tai_code,
                    vessel_key=vessel_key,
                    source="visual",
                    camera_lat=cam_lat,
                    camera_lon=cam_lon,
                )
            )

            if not should_send:
                continue

            # Enrich detection with correlated vessel name from API
            if correlated_name:
                detection["vessel_name"] = correlated_name
            if upgraded_conf:
                detection["confidence"] = 0.95

            # Save detection image
            image_path = self._chatsurfer.save_detection_image(
                frame, tai_code, detection
            )

            # Report to ChatSurfer
            report = self._chatsurfer.report_detection(
                detection=detection,
                tai=tai_code,
                image_path=image_path,
            )

            if report:
                self._deconfliction.record_report(
                    tai=tai_code,
                    vessel_key=correlated_name or vessel_key,
                    source="visual",
                    platform=report.platform,
                    confidence=report.confidence.value,
                    serial=report.format_serial(),
                    vessel_name=correlated_name,
                    camera_id=feed_id,
                    lat=cam_lat,
                    lon=cam_lon,
                )
                logger.info(f"Reported: {report.to_tacrep_string()}")

    def _simulate_detection(self, frame: np.ndarray, feed: CameraFeed) -> list:
        """
        Placeholder detection for testing.

        Replace with actual YOLOv8 detection in production.
        """
        # Only generate simulated detections occasionally for testing
        import random
        if random.random() > 0.1:  # 10% chance
            return []

        # Simulated detection
        h, w = frame.shape[:2]
        return [{
            "vessel_class": "Olympic",
            "vessel_name": "Tokitae",
            "confidence": 0.85,
            "bbox": [w//4, h//4, 3*w//4, 3*h//4],
            "direction": "INBOUND",
            "loading_state": "OFFLOADING",
            "vehicle_count": 45,
        }]

    def get_status(self) -> Dict[str, Any]:
        """Get current platform status."""
        return {
            "running": self._running,
            "feeds": self._feed_manager.get_status() if self._feed_manager else {},
        }


def setup_logging(level: str = "INFO", log_file: Optional[str] = None):
    """Configure logging."""
    handlers = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def main():
    """Application entry point."""
    parser = argparse.ArgumentParser(
        description="Puget Sound OSINT Platform - Ferry Surveillance System"
    )
    parser.add_argument("-c", "--config", default="config/settings.yaml",
                        help="Configuration file path")
    parser.add_argument("--log-level", default="INFO",
                        help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    parser.add_argument("--log-file", default=None,
                        help="Log file path")
    parser.add_argument("--no-web", action="store_true",
                        help="Disable web UI")
    parser.add_argument("--port", type=int, default=8080,
                        help="Web UI port (default: 8080)")
    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_level, args.log_file)

    # Create and initialize application
    osint_app = PugetSoundOSINT(args.config)

    # Signal handlers
    def shutdown(signum, frame):
        logger.info("Shutdown signal received")
        osint_app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Initialize
    if not osint_app.initialize():
        logger.error("Initialization failed")
        sys.exit(1)

    # Start
    if not osint_app.start():
        logger.error("Startup failed")
        sys.exit(1)

    # Run web UI if enabled
    web_config = osint_app.config.get("web", {})
    if web_config.get("enabled", True) and not args.no_web:
        from .api.server import create_app, run_server

        fastapi_app = create_app(osint_app)
        host = web_config.get("host", "0.0.0.0")
        port = args.port or web_config.get("port", 8080)

        logger.info(f"Web UI: http://{host}:{port}")
        run_server(fastapi_app, host, port)
    else:
        # Run until interrupted
        logger.info("Platform running (no web UI). Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    osint_app.stop()


if __name__ == "__main__":
    main()
