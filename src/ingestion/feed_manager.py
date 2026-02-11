"""
Camera Feed Manager - Orchestrates polling of multiple camera feeds.

Handles:
- Concurrent polling of WSDOT and third-party cameras
- Frame storage with timestamps
- Detection pipeline integration
- ChatSurfer reporting on detection events
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from queue import Queue

import aiohttp
import cv2
import numpy as np
import yaml

logger = logging.getLogger(__name__)


@dataclass
class CameraFeed:
    """Single camera feed configuration and state."""
    name: str
    id: str
    url: str
    coordinates: Tuple[float, float]  # (lat, lon)
    refresh_sec: float = 30.0
    tai_code: Optional[str] = None
    terminal_id: Optional[int] = None
    enabled: bool = True  # Whether to poll this camera

    # Runtime state
    last_fetch: float = 0.0
    last_frame: Optional[np.ndarray] = None
    last_frame_time: Optional[datetime] = None
    consecutive_errors: int = 0
    is_online: bool = True


@dataclass
class FeedManagerConfig:
    """Feed manager configuration."""
    cameras_config_path: str = "config/cameras.yaml"
    storage_path: str = "./captures"
    save_all_frames: bool = True
    max_concurrent_fetches: int = 10
    request_timeout_sec: float = 15.0
    max_consecutive_errors: int = 5
    error_backoff_sec: float = 60.0


class FeedManager:
    """
    Manages concurrent polling of multiple camera feeds.

    Usage:
        manager = FeedManager(config)
        manager.set_detection_callback(on_frame_callback)
        manager.start()
        ...
        manager.stop()
    """

    def __init__(self, config: FeedManagerConfig):
        self.config = config
        self.feeds: Dict[str, CameraFeed] = {}

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Callbacks
        self._frame_callback: Optional[Callable] = None

        # Storage
        self._storage_path = Path(config.storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)

        # Load camera configs
        self._load_cameras()

    def _load_cameras(self):
        """Load camera configurations from YAML."""
        config_path = Path(self.config.cameras_config_path)
        if not config_path.exists():
            logger.warning(f"Camera config not found: {config_path}")
            return

        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Load cameras from all tiers (new format) and legacy sections
        enabled_count = 0
        sections = [
            "tier1_water_facing",
            "tier2_partial_water",
            "tier3_operational",
            "disabled",
            # Legacy support
            "wsdot_terminals",
            "third_party_cameras",
        ]
        for section in sections:
            for cam in config.get(section, []) or []:
                enabled = cam.get("enabled", True)
                coords = cam.get("coordinates", {})
                if not coords:
                    continue
                feed = CameraFeed(
                    name=cam["name"],
                    id=cam["id"],
                    url=cam["url"],
                    coordinates=(coords["lat"], coords["lon"]),
                    refresh_sec=cam.get("refresh_sec", 30),
                    tai_code=cam.get("tai_code"),
                    terminal_id=cam.get("terminal_id"),
                    enabled=enabled,
                )
                self.feeds[feed.id] = feed
                if enabled:
                    enabled_count += 1

        logger.info(f"Loaded {len(self.feeds)} camera feeds ({enabled_count} enabled)")

    def set_detection_callback(self, callback: Callable[[str, np.ndarray, CameraFeed], None]):
        """
        Set callback for when frames are captured.

        Callback signature: callback(feed_id, frame, feed)
        """
        self._frame_callback = callback

    def start(self):
        """Start the feed manager."""
        if self._running:
            return

        self._running = True
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()
        logger.info("Feed manager started")

    def stop(self):
        """Stop the feed manager."""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=10.0)
        logger.info("Feed manager stopped")

    def _run_loop(self):
        """Main worker loop running async event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._polling_loop())
        finally:
            self._loop.close()

    async def _polling_loop(self):
        """Async polling loop for all feeds."""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        ) as session:
            while self._running:
                # Find feeds ready for polling
                now = time.time()
                ready_feeds = [
                    feed for feed in self.feeds.values()
                    if self._should_poll(feed, now)
                ]

                if ready_feeds:
                    # Poll feeds concurrently with semaphore limit
                    sem = asyncio.Semaphore(self.config.max_concurrent_fetches)
                    tasks = [
                        self._fetch_feed(session, feed, sem)
                        for feed in ready_feeds
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Small sleep to prevent tight loop
                await asyncio.sleep(1.0)

    def _should_poll(self, feed: CameraFeed, now: float) -> bool:
        """Check if feed should be polled."""
        # Skip disabled feeds
        if not feed.enabled:
            return False

        # Check if enough time has passed
        if now - feed.last_fetch < feed.refresh_sec:
            return False

        # Check error backoff
        if feed.consecutive_errors >= self.config.max_consecutive_errors:
            # Apply exponential backoff
            backoff = self.config.error_backoff_sec * (2 ** (feed.consecutive_errors - self.config.max_consecutive_errors))
            if now - feed.last_fetch < backoff:
                return False

        return True

    async def _fetch_feed(self, session: aiohttp.ClientSession, feed: CameraFeed, sem: asyncio.Semaphore):
        """Fetch a single camera feed."""
        async with sem:
            feed.last_fetch = time.time()

            try:
                async with session.get(feed.url) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}")

                    # Read image data
                    data = await resp.read()

                    # Decode image
                    nparr = np.frombuffer(data, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                    if frame is None:
                        raise Exception("Failed to decode image")

                    # Update feed state
                    feed.last_frame = frame
                    feed.last_frame_time = datetime.now(timezone.utc)
                    feed.consecutive_errors = 0
                    feed.is_online = True

                    # Save frame if configured
                    if self.config.save_all_frames:
                        self._save_frame(feed, frame)

                    # Call detection callback
                    if self._frame_callback:
                        try:
                            self._frame_callback(feed.id, frame, feed)
                        except Exception as e:
                            logger.error(f"Frame callback error for {feed.id}: {e}")

                    logger.debug(f"Fetched {feed.id}: {frame.shape}")

            except Exception as e:
                feed.consecutive_errors += 1
                if feed.consecutive_errors == 1 or feed.consecutive_errors == self.config.max_consecutive_errors:
                    logger.warning(f"Feed {feed.id} error ({feed.consecutive_errors}): {e}")
                if feed.consecutive_errors >= self.config.max_consecutive_errors:
                    feed.is_online = False

    def _save_frame(self, feed: CameraFeed, frame: np.ndarray):
        """Save frame to storage."""
        # Organize by date and camera
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        feed_dir = self._storage_path / date_str / feed.id
        feed_dir.mkdir(parents=True, exist_ok=True)

        # Filename with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%H%M%S")
        filename = f"{feed.id}_{timestamp}.jpg"
        filepath = feed_dir / filename

        cv2.imwrite(str(filepath), frame)

    def get_feed(self, feed_id: str) -> Optional[CameraFeed]:
        """Get feed by ID."""
        return self.feeds.get(feed_id)

    def get_latest_frame(self, feed_id: str) -> Optional[Tuple[np.ndarray, datetime]]:
        """Get latest frame and timestamp for a feed."""
        feed = self.feeds.get(feed_id)
        if feed and feed.last_frame is not None:
            return (feed.last_frame, feed.last_frame_time)
        return None

    def get_status(self) -> Dict:
        """Get status of all feeds."""
        return {
            feed_id: {
                "name": feed.name,
                "enabled": feed.enabled,
                "online": feed.is_online,
                "last_update": feed.last_frame_time.isoformat() if feed.last_frame_time else None,
                "errors": feed.consecutive_errors,
                "tai_code": feed.tai_code,
            }
            for feed_id, feed in self.feeds.items()
        }

    def get_tai_feeds(self, tai_code: str) -> List[CameraFeed]:
        """Get all feeds for a specific TAI code."""
        return [f for f in self.feeds.values() if f.tai_code == tai_code]
