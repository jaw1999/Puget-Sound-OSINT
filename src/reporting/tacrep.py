"""
TACREP (Tactical Report) Generator

Generates structured observation reports in the format:
CALLSIGN//SERIALIZED TACREP #//# OF TARGETS//PLATFORM//TAI//TIMESTAMP (Z)//REM:

Example:
PR01//I005//2//PROBABLE//ORCA//BALDER//0211//REM: ACTUAL RACCOON OFFLOADING
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import re


class ConfidenceLevel(Enum):
    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    POSSIBLE = "POSSIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class TacrepReport:
    """Structured tactical report"""
    callsign: str
    serial_number: int
    num_targets: int
    confidence: ConfidenceLevel
    platform: str  # Vessel class code (ORCA, WHALE, etc.) or specific vessel
    tai: str       # Target Area of Interest code (BALDER, THOR, etc.)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    remarks: str = ""

    # Optional detailed fields
    vessel_name: Optional[str] = None
    direction: Optional[str] = None  # INBOUND, OUTBOUND, DOCKED
    loading_state: Optional[str] = None  # LOADING, OFFLOADING, IDLE
    vehicle_count: Optional[int] = None

    def format_serial(self) -> str:
        """Format serial number as I-prefixed string (I001, I002, etc.)"""
        return f"I{self.serial_number:03d}"

    def format_timestamp(self) -> str:
        """Format timestamp as HHMM Zulu"""
        return self.timestamp.strftime("%H%M")

    def to_tacrep_string(self) -> str:
        """Generate the full TACREP formatted string"""
        parts = [
            self.callsign,
            self.format_serial(),
            str(self.num_targets),
            self.confidence.value,
            self.platform,
            self.tai,
            self.format_timestamp(),
            f"REM: {self.remarks}" if self.remarks else "REM:"
        ]
        return "//".join(parts)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "callsign": self.callsign,
            "serial": self.format_serial(),
            "num_targets": self.num_targets,
            "confidence": self.confidence.value,
            "platform": self.platform,
            "tai": self.tai,
            "timestamp_z": self.format_timestamp(),
            "timestamp_iso": self.timestamp.isoformat(),
            "remarks": self.remarks,
            "vessel_name": self.vessel_name,
            "direction": self.direction,
            "loading_state": self.loading_state,
            "vehicle_count": self.vehicle_count,
            "formatted": self.to_tacrep_string()
        }


class TacrepGenerator:
    """
    Generates TACREP reports from detection events
    """

    def __init__(self, callsign: str = "PR01", serial_prefix: str = "I"):
        self.callsign = callsign
        self.serial_prefix = serial_prefix
        self._serial_counter = 0

    def _next_serial(self) -> int:
        """Get next serial number"""
        self._serial_counter += 1
        return self._serial_counter

    def reset_serial(self, value: int = 0):
        """Reset serial counter (e.g., for new shift)"""
        self._serial_counter = value

    def generate_checkin(self) -> str:
        """Generate check-in message"""
        timestamp = datetime.now(timezone.utc).strftime("%H%MZ")
        return f'"{self.callsign} ONSTA {timestamp}"'

    def generate_checkout(self) -> str:
        """Generate check-off message"""
        timestamp = datetime.now(timezone.utc).strftime("%H%MZ")
        return f'"{self.callsign} OFF-STA {timestamp}"'

    def create_report(
        self,
        num_targets: int,
        confidence: ConfidenceLevel,
        platform: str,
        tai: str,
        remarks: str = "",
        vessel_name: Optional[str] = None,
        direction: Optional[str] = None,
        loading_state: Optional[str] = None,
        vehicle_count: Optional[int] = None,
        timestamp: Optional[datetime] = None
    ) -> TacrepReport:
        """
        Create a new TACREP report

        Args:
            num_targets: Number of targets observed
            confidence: Confidence level (CONFIRMED, PROBABLE, POSSIBLE, UNKNOWN)
            platform: Vessel class/type code
            tai: Target Area of Interest code
            remarks: Additional observations
            vessel_name: Specific vessel name if identified
            direction: INBOUND, OUTBOUND, or DOCKED
            loading_state: LOADING, OFFLOADING, or IDLE
            vehicle_count: Number of vehicles observed
            timestamp: Report timestamp (defaults to now)

        Returns:
            TacrepReport object
        """
        return TacrepReport(
            callsign=self.callsign,
            serial_number=self._next_serial(),
            num_targets=num_targets,
            confidence=confidence,
            platform=platform,
            tai=tai,
            timestamp=timestamp or datetime.now(timezone.utc),
            remarks=remarks,
            vessel_name=vessel_name,
            direction=direction,
            loading_state=loading_state,
            vehicle_count=vehicle_count
        )

    def from_detection(
        self,
        detection: dict,
        tai: str,
        platform_mapping: dict = None
    ) -> TacrepReport:
        """
        Create TACREP from a detection event

        Args:
            detection: Detection dictionary with keys like:
                - vessel_class: detected vessel class
                - vessel_name: specific vessel if identified
                - confidence: detection confidence (0-1)
                - direction: vessel direction
                - loading_state: loading/offloading status
                - bbox: bounding box
            tai: Target Area of Interest code
            platform_mapping: Optional mapping of vessel classes to platform codes

        Returns:
            TacrepReport object
        """
        # Map confidence score to level
        conf_score = detection.get("confidence", 0)
        if conf_score >= 0.9:
            confidence = ConfidenceLevel.CONFIRMED
        elif conf_score >= 0.7:
            confidence = ConfidenceLevel.PROBABLE
        elif conf_score >= 0.5:
            confidence = ConfidenceLevel.POSSIBLE
        else:
            confidence = ConfidenceLevel.UNKNOWN

        # Map vessel class to platform code
        vessel_class = detection.get("vessel_class", "UNKNOWN")
        platform = vessel_class
        if platform_mapping and vessel_class in platform_mapping:
            platform = platform_mapping[vessel_class]

        # Build remarks from detection details
        remarks_parts = []
        if detection.get("vessel_name"):
            remarks_parts.append(f"VES {detection['vessel_name'].upper()}")
        if detection.get("direction"):
            remarks_parts.append(detection["direction"].upper())
        if detection.get("loading_state"):
            remarks_parts.append(detection["loading_state"].upper())
        if detection.get("vehicle_count"):
            remarks_parts.append(f"{detection['vehicle_count']} VICS")

        remarks = " ".join(remarks_parts)

        return self.create_report(
            num_targets=1,
            confidence=confidence,
            platform=platform,
            tai=tai,
            remarks=remarks,
            vessel_name=detection.get("vessel_name"),
            direction=detection.get("direction"),
            loading_state=detection.get("loading_state"),
            vehicle_count=detection.get("vehicle_count")
        )


# Utility functions for parsing TACREP strings
def parse_tacrep(tacrep_string: str) -> Optional[dict]:
    """
    Parse a TACREP formatted string back into components

    Args:
        tacrep_string: String like "PR01//I005//2//PROBABLE//ORCA//BALDER//0211//REM: text"

    Returns:
        Dictionary with parsed components or None if invalid
    """
    pattern = r"^([A-Z0-9]+)//([A-Z]\d{3})//(\d+)//([A-Z]+)//([A-Z]+)//([A-Z]+)//(\d{4})//REM:(.*)$"
    match = re.match(pattern, tacrep_string)

    if not match:
        return None

    return {
        "callsign": match.group(1),
        "serial": match.group(2),
        "num_targets": int(match.group(3)),
        "confidence": match.group(4),
        "platform": match.group(5),
        "tai": match.group(6),
        "timestamp_z": match.group(7),
        "remarks": match.group(8).strip()
    }
