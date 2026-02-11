"""
ChatSurfer Integration - Automated TACREP streaming.

Streams detection reports to ChatSurfer chat system using direct API calls,
matching the CCTV-viewer project pattern with TACREP message format.

Message format:
CALLSIGN//SERIAL//# TARGETS//CONFIDENCE//PLATFORM//TAI//TIMESTAMP//REM: remarks
"""

import asyncio
import json
import logging
import os
import threading
import time
import requests
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue, Empty
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin

from .tacrep import TacrepReport, TacrepGenerator, ConfidenceLevel

logger = logging.getLogger(__name__)


@dataclass
class ChatSurferConfig:
    """ChatSurfer output configuration - matches CCTV project pattern."""
    enabled: bool = True
    callsign: str = "PR01"

    # Output mode: "chatsurfer", "file", "stdout"
    mode: str = "stdout"

    # ChatSurfer API settings (matches CCTV-viewer)
    server_url: str = "https://chatsurfer.nro.mil"
    session: str = ""  # SESSION cookie value
    room: str = ""     # Chat room name
    nickname: str = "OSINT_Bot"
    domain: str = "chatsurferxmppunclass"
    classification: str = "UNCLASSIFIED//FOUO"

    # File output settings (backup/debug)
    output_file: str = "reports/tacreps.log"

    # Image hosting settings
    image_base_url: str = "http://localhost:8080/images/"
    image_storage_path: str = "./captures/"

    # Rate limiting - min seconds between reports for same TAI
    min_report_interval_sec: float = 30.0

    # Retry settings
    max_retries: int = 3
    retry_delay_sec: float = 1.0

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "callsign": self.callsign,
            "mode": self.mode,
            "server_url": self.server_url,
            "session": self.session,
            "room": self.room,
            "nickname": self.nickname,
            "domain": self.domain,
            "classification": self.classification,
            "output_file": self.output_file,
            "image_base_url": self.image_base_url,
            "min_report_interval_sec": self.min_report_interval_sec,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ChatSurferConfig":
        return cls(
            enabled=data.get("enabled", True),
            callsign=data.get("callsign", "PR01"),
            mode=data.get("mode", "stdout"),
            server_url=data.get("server_url", "https://chatsurfer.nro.mil"),
            session=data.get("session", ""),
            room=data.get("room", ""),
            nickname=data.get("nickname", "OSINT_Bot"),
            domain=data.get("domain", "chatsurferxmppunclass"),
            classification=data.get("classification", "UNCLASSIFIED//FOUO"),
            output_file=data.get("output_file", "reports/tacreps.log"),
            image_base_url=data.get("image_base_url", "http://localhost:8080/images/"),
            min_report_interval_sec=data.get("min_report_interval_sec", 30.0),
        )


def send_chatsurfer_message(
    message: str,
    config: ChatSurferConfig,
    image_url: Optional[str] = None
) -> bool:
    """
    Send message to ChatSurfer API - matches CCTV-viewer pattern.

    Args:
        message: TACREP formatted message string
        config: ChatSurfer configuration
        image_url: Optional URL to detection image

    Returns:
        True on success, False on failure
    """
    if not config.session or not config.room:
        logger.warning("ChatSurfer session or room not configured")
        return False

    url = f"{config.server_url}/api/chatserver/message"
    headers = {
        "cookie": f"SESSION={config.session}",
        "Content-Type": "application/json"
    }

    # Build message with optional image URL
    full_message = message
    if image_url:
        full_message += f"\n[IMG] {image_url}"

    payload = {
        "classification": config.classification,
        "message": full_message,
        "domainId": config.domain,
        "nickName": config.nickname,
        "roomName": config.room
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            verify=False,  # ChatSurfer may use self-signed certs
            timeout=10
        )

        if response.status_code in [200, 204]:
            logger.info(f"ChatSurfer message sent: {message[:50]}...")
            return True
        else:
            logger.error(f"ChatSurfer error: {response.status_code} - {response.text}")
            return False

    except requests.exceptions.Timeout:
        logger.error("ChatSurfer request timed out")
        return False
    except requests.exceptions.ConnectionError as e:
        logger.error(f"ChatSurfer connection error: {e}")
        return False
    except Exception as e:
        logger.error(f"ChatSurfer error: {e}")
        return False


class ChatSurferClient:
    """
    Automated TACREP report streaming to ChatSurfer.

    Matches CCTV-viewer project pattern with direct API calls,
    using TACREP message format.

    Usage:
        config = ChatSurferConfig(
            mode="chatsurfer",
            session="your-session-cookie",
            room="your-room-name"
        )
        client = ChatSurferClient(config)
        client.start()

        # When detection occurs:
        client.report_detection(
            detection=detection_dict,
            tai="BALDER",
            image_path="/path/to/capture.jpg"
        )

        client.stop()
    """

    def __init__(self, config: ChatSurferConfig):
        self.config = config
        self.tacrep_gen = TacrepGenerator(callsign=config.callsign)

        self._queue: Queue = Queue()
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        # Rate limiting per TAI
        self._last_report_time: Dict[str, float] = {}

        # Platform code mapping for WSF vessels
        self._platform_map = {
            "JUMBO_MARK_II": "WHALE",
            "SUPER": "EAGLE",
            "ISSAQUAH": "SALMON",
            "OLYMPIC": "ORCA",
            "KWA_DI_TABIL": "SEAL",
            "Olympic": "ORCA",
            "Jumbo Mark II": "WHALE",
            "Issaquah": "SALMON",
            "Super": "EAGLE",
            "Kwa-di Tabil": "SEAL",
        }

        # Ensure output directory exists
        Path(config.output_file).parent.mkdir(parents=True, exist_ok=True)

    def start(self):
        """Start the ChatSurfer client."""
        if not self.config.enabled:
            logger.info("ChatSurfer disabled in config")
            return

        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        logger.info(f"ChatSurfer client started (mode={self.config.mode})")

    def stop(self):
        """Stop the ChatSurfer client."""
        self._running = False
        if self._worker_thread:
            self._queue.put(None)  # Signal shutdown
            self._worker_thread.join(timeout=5.0)
        logger.info("ChatSurfer client stopped")

    def _worker_loop(self):
        """Background worker processing report queue."""
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
                if item is None:
                    break

                report, image_url = item
                self._send_report(report, image_url)

            except Empty:
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")

    def _send_report(self, report: TacrepReport, image_url: Optional[str]):
        """Send report based on configured mode."""
        message = report.to_tacrep_string()

        # Always log to file as backup
        self._write_to_file(message, image_url)

        mode = self.config.mode.lower()

        # Always send to ChatSurfer when session+room are configured
        if self.config.session and self.config.room:
            success = send_chatsurfer_message(message, self.config, image_url)
            if success:
                logger.info(f"TACREP sent to ChatSurfer: {report.format_serial()} -> {report.tai}")
            else:
                logger.error(f"Failed to send TACREP to ChatSurfer: {report.format_serial()}")

        if mode == "stdout":
            self._print_report(message, image_url)

        # File mode just uses the backup write above

    def _write_to_file(self, message: str, image_url: Optional[str]):
        """Write report to log file."""
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
            line = f"[{timestamp}] {message}"
            if image_url:
                line += f" | IMG: {image_url}"
            line += "\n"

            with open(self.config.output_file, "a") as f:
                f.write(line)
        except Exception as e:
            logger.error(f"File write error: {e}")

    def _print_report(self, message: str, image_url: Optional[str]):
        """Print report to stdout."""
        output = f"\n{'='*60}\n[TACREP] {message}"
        if image_url:
            output += f"\n[IMAGE] {image_url}"
        output += f"\n{'='*60}\n"
        print(output)

    def update_config(self, **kwargs):
        """Update configuration dynamically."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

        # Update callsign in generator if changed
        if "callsign" in kwargs:
            self.tacrep_gen.callsign = kwargs["callsign"]

    def check_in(self):
        """Send check-in message."""
        msg = self.tacrep_gen.generate_checkin()

        if self.config.session and self.config.room:
            send_chatsurfer_message(msg, self.config)

        print(f"\n[CHECK-IN] {msg}\n")
        self._write_to_file(msg, None)
        return msg

    def check_out(self):
        """Send check-off message."""
        msg = self.tacrep_gen.generate_checkout()

        if self.config.session and self.config.room:
            send_chatsurfer_message(msg, self.config)

        print(f"\n[CHECK-OUT] {msg}\n")
        self._write_to_file(msg, None)
        return msg

    def send_report(self, report: TacrepReport, image_url: Optional[str] = None):
        """Queue a TACREP report for sending."""
        self._queue.put((report, image_url))

    def report_detection(
        self,
        detection: dict,
        tai: str,
        image_path: Optional[str] = None,
        force: bool = False
    ) -> Optional[TacrepReport]:
        """
        Report a detection event to ChatSurfer.

        Args:
            detection: Detection dict with vessel info
            tai: Target Area of Interest code
            image_path: Local path to captured frame
            force: Bypass rate limiting

        Returns:
            TacrepReport if sent, None if rate-limited
        """
        # Rate limiting check
        now = time.time()
        last_time = self._last_report_time.get(tai, 0)
        if not force and (now - last_time) < self.config.min_report_interval_sec:
            return None

        self._last_report_time[tai] = now

        # Generate image URL
        image_url = None
        if image_path:
            image_url = self._get_image_url(image_path)

        # Create TACREP from detection
        report = self.tacrep_gen.from_detection(
            detection=detection,
            tai=tai,
            platform_mapping=self._platform_map
        )

        # Queue for sending
        self._queue.put((report, image_url))

        return report

    def _get_image_url(self, image_path: str) -> str:
        """Convert local image path to accessible URL."""
        filename = os.path.basename(image_path)
        return urljoin(self.config.image_base_url, filename)

    def save_detection_image(
        self,
        frame,  # numpy array
        tai: str,
        detection: dict = None
    ) -> str:
        """
        Save detection frame and return path.

        Args:
            frame: CV2/numpy image array
            tai: TAI code for filename
            detection: Optional detection to draw bbox

        Returns:
            Path to saved image
        """
        import cv2

        # Create storage directory
        storage_path = Path(self.config.image_storage_path)
        storage_path.mkdir(parents=True, exist_ok=True)

        # Generate filename with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{tai}_{timestamp}.jpg"
        filepath = storage_path / filename

        # Draw detection bbox if provided
        if detection and "bbox" in detection:
            frame = frame.copy()
            bbox = detection["bbox"]
            if isinstance(bbox, dict):
                x1, y1 = int(bbox.get("x1", 0)), int(bbox.get("y1", 0))
                x2, y2 = int(bbox.get("x2", 0)), int(bbox.get("y2", 0))
            else:
                x1, y1, x2, y2 = map(int, bbox[:4])

            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

            label = detection.get("vessel_name", detection.get("vessel_class", "VESSEL"))
            cv2.putText(frame, str(label), (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        # Save image
        cv2.imwrite(str(filepath), frame)

        return str(filepath)

    def send_test_message(self) -> bool:
        """Send a test message to verify connection."""
        test_msg = f"[TEST] {self.config.callsign} connection test from Puget Sound OSINT"

        if self.config.mode.lower() == "chatsurfer":
            return send_chatsurfer_message(test_msg, self.config)
        else:
            print(f"\n[TEST] {test_msg}\n")
            return True
