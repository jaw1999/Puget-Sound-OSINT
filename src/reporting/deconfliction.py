"""
TACREP Deconfliction Layer.

Prevents duplicate reports when both visual detection (YOLO camera feeds)
and vessel API (WSDOT positions) observe the same vessel in the same TAI.

Strategy:
- Track reports by composite key: (TAI, vessel_identity)
- Correlate visual detections with nearby API-tracked vessels
- Suppress duplicates within a configurable time window
- When both sources agree, upgrade confidence to CONFIRMED
- API data provides identity; visual data provides confirmation
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import math

logger = logging.getLogger(__name__)


@dataclass
class ReportRecord:
    """Tracks a single reported observation."""
    tai: str
    vessel_key: str           # Vessel name or "VISUAL_<camera_id>"
    source: str               # "api" or "visual"
    platform: str             # Platform code (ORCA, WHALE, etc.)
    confidence: str           # CONFIRMED, PROBABLE, POSSIBLE, UNKNOWN
    timestamp: float          # time.time()
    serial: str               # TACREP serial (I001, etc.)
    vessel_name: Optional[str] = None
    camera_id: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    correlated: bool = False  # True if matched across sources


class TacrepDeconfliction:
    """
    Deconfliction engine for TACREP reports from multiple sources.

    Prevents the same vessel from generating duplicate reports when
    observed by both camera detection and the vessel tracking API.

    Usage:
        decon = TacrepDeconfliction(suppress_window_sec=120)

        # Before sending a TACREP, check if it's a duplicate:
        if decon.should_report(tai, vessel_key, source, ...):
            # Send the report
            decon.record_report(tai, vessel_key, source, ...)
    """

    def __init__(
        self,
        suppress_window_sec: float = 120.0,
        correlation_radius_nm: float = 2.0,
        max_records: int = 500,
    ):
        """
        Args:
            suppress_window_sec: Time window to suppress duplicate reports
                for the same vessel in the same TAI.
            correlation_radius_nm: Nautical miles radius to correlate
                visual detections with API vessel positions.
            max_records: Max report records to retain.
        """
        self.suppress_window_sec = suppress_window_sec
        self.correlation_radius_nm = correlation_radius_nm
        self.max_records = max_records

        # Recent reports keyed by (tai, vessel_key)
        self._reports: Dict[Tuple[str, str], ReportRecord] = {}

        # API vessel positions cache for correlation
        # keyed by vessel_name -> {lat, lon, tai, at_dock, timestamp}
        self._api_vessel_cache: Dict[str, dict] = {}

    def update_api_vessels(self, vessels: Dict[str, dict]):
        """
        Update cached API vessel positions for correlation.

        Call this whenever the vessel tracker refreshes.

        Args:
            vessels: Dict of vessel data from /api/vessels,
                     keyed by vessel_id or vessel_name.
        """
        now = time.time()
        for vid, v in vessels.items():
            name = v.get("name", str(vid))
            self._api_vessel_cache[name] = {
                "lat": v.get("latitude", 0),
                "lon": v.get("longitude", 0),
                "at_dock": v.get("at_dock", False),
                "departing_terminal": v.get("departing_terminal"),
                "arriving_terminal": v.get("arriving_terminal"),
                "vessel_class": v.get("vessel_class"),
                "speed": v.get("speed", 0),
                "timestamp": now,
            }

    def correlate_visual_with_api(
        self,
        camera_lat: float,
        camera_lon: float,
    ) -> Optional[str]:
        """
        Find an API-tracked vessel near a camera location.

        Used to identify which vessel a visual detection likely corresponds to.

        Args:
            camera_lat: Camera latitude
            camera_lon: Camera longitude

        Returns:
            Vessel name if a nearby vessel is found, None otherwise.
        """
        now = time.time()
        best_name = None
        best_dist = float("inf")

        for name, v in self._api_vessel_cache.items():
            # Skip stale entries (> 60 seconds old)
            if now - v["timestamp"] > 60:
                continue

            dist = self._distance_nm(camera_lat, camera_lon, v["lat"], v["lon"])
            if dist < self.correlation_radius_nm and dist < best_dist:
                best_dist = dist
                best_name = name

        return best_name

    def should_report(
        self,
        tai: str,
        vessel_key: str,
        source: str,
        camera_lat: Optional[float] = None,
        camera_lon: Optional[float] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Check whether a detection should generate a new TACREP.

        Returns:
            Tuple of (should_send, correlated_vessel_name, upgraded_confidence)
            - should_send: True if this is a new observation
            - correlated_vessel_name: API vessel name if visual was correlated
            - upgraded_confidence: "CONFIRMED" if both sources agree, else None
        """
        now = time.time()

        correlated_vessel = None
        upgraded_confidence = None

        # For visual detections, try to correlate with API vessels
        if source == "visual" and camera_lat and camera_lon:
            correlated_vessel = self.correlate_visual_with_api(camera_lat, camera_lon)
            if correlated_vessel:
                # Use the real vessel name as the key instead of generic camera ID
                vessel_key = correlated_vessel

        # Check for existing report in the suppress window
        key = (tai, vessel_key)
        existing = self._reports.get(key)

        if existing:
            elapsed = now - existing.timestamp
            if elapsed < self.suppress_window_sec:
                # Within suppress window - check if this is a cross-source correlation
                if existing.source != source and not existing.correlated:
                    # Different source confirming the same vessel -> upgrade & note it
                    existing.correlated = True
                    existing.confidence = "CONFIRMED"
                    upgraded_confidence = "CONFIRMED"
                    logger.info(
                        f"Deconfliction: correlated {vessel_key} in {tai} "
                        f"({existing.source} + {source}) -> CONFIRMED"
                    )
                    # Don't send a new TACREP, but return the upgrade
                    return False, correlated_vessel, upgraded_confidence
                else:
                    # Same source or already correlated -> suppress
                    logger.debug(
                        f"Deconfliction: suppressed duplicate {vessel_key} in {tai} "
                        f"({elapsed:.0f}s < {self.suppress_window_sec}s)"
                    )
                    return False, correlated_vessel, None
            # Window expired, allow new report

        return True, correlated_vessel, upgraded_confidence

    def record_report(
        self,
        tai: str,
        vessel_key: str,
        source: str,
        platform: str,
        confidence: str,
        serial: str,
        vessel_name: Optional[str] = None,
        camera_id: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ):
        """
        Record that a TACREP was sent for dedup tracking.

        Call this AFTER successfully sending a report.
        """
        key = (tai, vessel_key)
        self._reports[key] = ReportRecord(
            tai=tai,
            vessel_key=vessel_key,
            source=source,
            platform=platform,
            confidence=confidence,
            timestamp=time.time(),
            serial=serial,
            vessel_name=vessel_name,
            camera_id=camera_id,
            lat=lat,
            lon=lon,
        )

        # Prune old records
        self._prune()

    def _prune(self):
        """Remove expired records."""
        now = time.time()
        expired = [
            k for k, v in self._reports.items()
            if now - v.timestamp > self.suppress_window_sec * 3
        ]
        for k in expired:
            del self._reports[k]

        # Hard cap
        if len(self._reports) > self.max_records:
            sorted_keys = sorted(self._reports, key=lambda k: self._reports[k].timestamp)
            for k in sorted_keys[:len(self._reports) - self.max_records]:
                del self._reports[k]

    def get_active_reports(self) -> List[dict]:
        """Get currently active (non-expired) report records."""
        now = time.time()
        return [
            {
                "tai": r.tai,
                "vessel_key": r.vessel_key,
                "source": r.source,
                "platform": r.platform,
                "confidence": r.confidence,
                "age_sec": round(now - r.timestamp),
                "correlated": r.correlated,
                "vessel_name": r.vessel_name,
            }
            for r in self._reports.values()
            if now - r.timestamp < self.suppress_window_sec
        ]

    @staticmethod
    def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Haversine distance in nautical miles."""
        R = 3440.065
        dLat = math.radians(lat2 - lat1)
        dLon = math.radians(lon2 - lon1)
        a = (math.sin(dLat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dLon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
