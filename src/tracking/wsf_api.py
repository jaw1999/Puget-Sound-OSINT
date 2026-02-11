"""
WSDOT Washington State Ferries Vessels API Client.

Provides real-time vessel positions and information from the WSF API.
API Documentation: https://www.wsdot.wa.gov/ferries/api/vessels/documentation/rest.html

Endpoints:
- /vessellocations - Real-time GPS positions (~5 second updates)
- /vesselbasics - Vessel names, IDs, class info
- /vesselverbose - Combined vessel data with current status
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from enum import Enum

import aiohttp

logger = logging.getLogger(__name__)

# WSDOT Vessels API base URL
BASE_URL = "https://www.wsdot.wa.gov/ferries/api/vessels/rest"


class VesselClass(Enum):
    """WSF vessel classes with platform codes for TACREP."""
    JUMBO_MARK_II = "WHALE"      # Tacoma, Wenatchee, Puyallup
    SUPER = "EAGLE"              # Hyak, Kaleetan, Yakima, Elwha
    ISSAQUAH = "SALMON"          # Issaquah, Kittitas, Cathlamet, Kitsap, Sealth, Chelan
    OLYMPIC = "ORCA"             # Tokitae, Samish, Chimacum, Suquamish
    KWA_DI_TABIL = "SEAL"        # Chetzemoka, Kennewick, Salish
    UNKNOWN = "UNKNOWN"


# Vessel name to class mapping
VESSEL_CLASSES = {
    # Jumbo Mark II
    "Tacoma": VesselClass.JUMBO_MARK_II,
    "Wenatchee": VesselClass.JUMBO_MARK_II,
    "Puyallup": VesselClass.JUMBO_MARK_II,
    # Super class
    "Hyak": VesselClass.SUPER,
    "Kaleetan": VesselClass.SUPER,
    "Yakima": VesselClass.SUPER,
    "Elwha": VesselClass.SUPER,
    "Walla Walla": VesselClass.SUPER,
    "Spokane": VesselClass.SUPER,
    # Issaquah class
    "Issaquah": VesselClass.ISSAQUAH,
    "Kittitas": VesselClass.ISSAQUAH,
    "Cathlamet": VesselClass.ISSAQUAH,
    "Kitsap": VesselClass.ISSAQUAH,
    "Sealth": VesselClass.ISSAQUAH,
    "Chelan": VesselClass.ISSAQUAH,
    # Olympic class
    "Tokitae": VesselClass.OLYMPIC,
    "Samish": VesselClass.OLYMPIC,
    "Chimacum": VesselClass.OLYMPIC,
    "Suquamish": VesselClass.OLYMPIC,
    # Kwa-di Tabil class
    "Chetzemoka": VesselClass.KWA_DI_TABIL,
    "Kennewick": VesselClass.KWA_DI_TABIL,
    "Salish": VesselClass.KWA_DI_TABIL,
}


@dataclass
class VesselPosition:
    """Real-time vessel position data."""
    vessel_id: int
    vessel_name: str
    mmsi: Optional[int]
    latitude: float
    longitude: float
    speed: float  # knots
    heading: float  # degrees
    in_service: bool
    at_dock: bool
    departing_terminal_id: Optional[int]
    departing_terminal_name: Optional[str]
    arriving_terminal_id: Optional[int]
    arriving_terminal_name: Optional[str]
    scheduled_departure: Optional[datetime]
    eta: Optional[datetime]
    eta_source: Optional[str]  # "Schedule" or "Estimated"
    left_dock: Optional[datetime]
    timestamp: datetime
    vessel_class: VesselClass = field(default=VesselClass.UNKNOWN)
    platform_code: str = field(default="UNKNOWN")

    def __post_init__(self):
        """Set vessel class and platform code based on name."""
        self.vessel_class = VESSEL_CLASSES.get(self.vessel_name, VesselClass.UNKNOWN)
        self.platform_code = self.vessel_class.value


@dataclass
class VesselInfo:
    """Static vessel information."""
    vessel_id: int
    vessel_name: str
    vessel_abbrev: str
    vessel_class_id: int
    vessel_class_name: str
    length: float  # feet
    beam: float  # feet
    horsepower: int
    max_speed: float  # knots
    passenger_capacity: int
    vehicle_capacity: int
    tall_vehicle_capacity: int
    ada_capacity: int
    year_built: int
    year_rebuilt: Optional[int]
    vessel_class: VesselClass = field(default=VesselClass.UNKNOWN)
    platform_code: str = field(default="UNKNOWN")

    def __post_init__(self):
        """Set vessel class and platform code based on name."""
        self.vessel_class = VESSEL_CLASSES.get(self.vessel_name, VesselClass.UNKNOWN)
        self.platform_code = self.vessel_class.value


class WSFVesselsClient:
    """
    Client for WSDOT Washington State Ferries Vessels API.

    Usage:
        client = WSFVesselsClient(api_key="your-api-key")

        # Get all vessel positions
        positions = await client.get_vessel_locations()

        # Get vessel info
        vessels = await client.get_vessel_basics()
    """

    def __init__(self, api_key: str, timeout: float = 15.0):
        """
        Initialize the WSF API client.

        Args:
            api_key: WSDOT Traveler API access code
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None

        # Cache for vessel info (rarely changes)
        self._vessel_info_cache: Dict[int, VesselInfo] = {}
        self._last_cache_update: Optional[datetime] = None
        self._cache_ttl_hours = 24

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, endpoint: str) -> dict:
        """Make API request."""
        session = await self._get_session()
        url = f"{BASE_URL}/{endpoint}?apiaccesscode={self.api_key}"

        try:
            async with session.get(url) as resp:
                if resp.status == 401:
                    raise ValueError("Invalid API key")
                if resp.status != 200:
                    raise Exception(f"API error: HTTP {resp.status}")
                return await resp.json()
        except aiohttp.ClientError as e:
            logger.error(f"WSF API request failed: {e}")
            raise

    def _parse_datetime(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse WSDOT datetime format."""
        if not date_str:
            return None
        try:
            # WSDOT uses format like "/Date(1234567890000-0800)/"
            if date_str.startswith("/Date("):
                # Extract timestamp in milliseconds
                ts_str = date_str[6:-2]  # Remove "/Date(" and ")/"
                # Handle timezone offset
                if "-" in ts_str[1:]:
                    ts_ms = int(ts_str.split("-")[0])
                elif "+" in ts_str:
                    ts_ms = int(ts_str.split("+")[0])
                else:
                    ts_ms = int(ts_str)
                return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            return None
        except (ValueError, IndexError):
            return None

    async def get_vessel_locations(self) -> List[VesselPosition]:
        """
        Get real-time positions of all active vessels.

        Returns:
            List of VesselPosition objects with current locations
        """
        data = await self._request("vessellocations")

        positions = []
        for item in data:
            try:
                pos = VesselPosition(
                    vessel_id=item.get("VesselID", 0),
                    vessel_name=item.get("VesselName", "Unknown"),
                    mmsi=item.get("Mmsi"),
                    latitude=item.get("Latitude", 0.0),
                    longitude=item.get("Longitude", 0.0),
                    speed=item.get("Speed", 0.0),
                    heading=item.get("Heading", 0.0),
                    in_service=item.get("InService", False),
                    at_dock=item.get("AtDock", False),
                    departing_terminal_id=item.get("DepartingTerminalID"),
                    departing_terminal_name=item.get("DepartingTerminalName"),
                    arriving_terminal_id=item.get("ArrivingTerminalID"),
                    arriving_terminal_name=item.get("ArrivingTerminalName"),
                    scheduled_departure=self._parse_datetime(item.get("ScheduledDeparture")),
                    eta=self._parse_datetime(item.get("Eta")),
                    eta_source=item.get("EtaSource"),
                    left_dock=self._parse_datetime(item.get("LeftDock")),
                    timestamp=datetime.now(timezone.utc),
                )
                positions.append(pos)
            except Exception as e:
                logger.warning(f"Failed to parse vessel location: {e}")

        logger.debug(f"Got {len(positions)} vessel positions")
        return positions

    async def get_vessel_basics(self, use_cache: bool = True) -> Dict[int, VesselInfo]:
        """
        Get basic information about all vessels.

        Args:
            use_cache: Whether to use cached data if available

        Returns:
            Dict mapping vessel_id to VesselInfo
        """
        # Check cache
        if use_cache and self._vessel_info_cache:
            if self._last_cache_update:
                age_hours = (datetime.now(timezone.utc) - self._last_cache_update).total_seconds() / 3600
                if age_hours < self._cache_ttl_hours:
                    return self._vessel_info_cache

        data = await self._request("vesselbasics")

        vessels = {}
        for item in data:
            try:
                info = VesselInfo(
                    vessel_id=item.get("VesselID", 0),
                    vessel_name=item.get("VesselName", "Unknown"),
                    vessel_abbrev=item.get("VesselAbbrev", ""),
                    vessel_class_id=item.get("Class", {}).get("ClassID", 0),
                    vessel_class_name=item.get("Class", {}).get("ClassName", "Unknown"),
                    length=item.get("Length", 0.0),
                    beam=item.get("Beam", 0.0),
                    horsepower=item.get("Horsepower", 0),
                    max_speed=item.get("MaxSpeed", 0.0),
                    passenger_capacity=item.get("PassengerCapacity", 0),
                    vehicle_capacity=item.get("VehicleCapacity", 0),
                    tall_vehicle_capacity=item.get("TallVehicleCapacity", 0),
                    ada_capacity=item.get("ADACapacity", 0),
                    year_built=item.get("YearBuilt", 0),
                    year_rebuilt=item.get("YearRebuilt"),
                )
                vessels[info.vessel_id] = info
            except Exception as e:
                logger.warning(f"Failed to parse vessel info: {e}")

        # Update cache
        self._vessel_info_cache = vessels
        self._last_cache_update = datetime.now(timezone.utc)

        logger.info(f"Loaded {len(vessels)} vessel info records")
        return vessels

    async def get_vessel_verbose(self) -> List[dict]:
        """
        Get verbose vessel data combining location and info.

        Returns:
            Raw API response with full vessel details
        """
        return await self._request("vesselverbose")

    async def get_active_vessels(self) -> List[VesselPosition]:
        """
        Get positions of only in-service vessels.

        Returns:
            List of VesselPosition for vessels currently in service
        """
        positions = await self.get_vessel_locations()
        return [p for p in positions if p.in_service]

    async def get_vessels_near_terminal(
        self,
        terminal_id: int,
        radius_nm: float = 1.0
    ) -> List[VesselPosition]:
        """
        Get vessels near a specific terminal.

        Args:
            terminal_id: WSDOT terminal ID
            radius_nm: Search radius in nautical miles

        Returns:
            List of vessels near the terminal
        """
        positions = await self.get_vessel_locations()

        near = []
        for pos in positions:
            if pos.departing_terminal_id == terminal_id:
                near.append(pos)
            elif pos.arriving_terminal_id == terminal_id:
                near.append(pos)

        return near


class VesselTracker:
    """
    Continuous vessel position tracker with callbacks.

    Usage:
        tracker = VesselTracker(api_key="your-key")
        tracker.on_position_update = my_callback
        await tracker.start()
        ...
        await tracker.stop()
    """

    def __init__(
        self,
        api_key: str,
        poll_interval: float = 5.0,
        only_active: bool = True
    ):
        """
        Initialize vessel tracker.

        Args:
            api_key: WSDOT API access code
            poll_interval: Seconds between position updates
            only_active: Only track in-service vessels
        """
        self.client = WSFVesselsClient(api_key)
        self.poll_interval = poll_interval
        self.only_active = only_active

        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Callbacks
        self.on_position_update: Optional[callable] = None
        self.on_error: Optional[callable] = None

        # Last known positions
        self.positions: Dict[int, VesselPosition] = {}
        self.vessel_info: Dict[int, VesselInfo] = {}

    async def start(self):
        """Start continuous tracking."""
        if self._running:
            return

        self._running = True

        # Load vessel info once
        try:
            self.vessel_info = await self.client.get_vessel_basics()
        except Exception as e:
            logger.error(f"Failed to load vessel info: {e}")

        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Vessel tracker started")

    async def stop(self):
        """Stop tracking."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.client.close()
        logger.info("Vessel tracker stopped")

    async def _poll_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                if self.only_active:
                    positions = await self.client.get_active_vessels()
                else:
                    positions = await self.client.get_vessel_locations()

                # Update stored positions
                for pos in positions:
                    self.positions[pos.vessel_id] = pos

                # Call callback
                if self.on_position_update:
                    try:
                        await self.on_position_update(positions)
                    except Exception as e:
                        logger.error(f"Position update callback error: {e}")

            except Exception as e:
                logger.error(f"Vessel tracking error: {e}")
                if self.on_error:
                    try:
                        await self.on_error(e)
                    except:
                        pass

            await asyncio.sleep(self.poll_interval)

    def get_vessel_by_name(self, name: str) -> Optional[VesselPosition]:
        """Get current position of vessel by name."""
        for pos in self.positions.values():
            if pos.vessel_name.lower() == name.lower():
                return pos
        return None

    def get_vessels_at_dock(self) -> List[VesselPosition]:
        """Get all vessels currently docked."""
        return [p for p in self.positions.values() if p.at_dock]

    def get_vessels_underway(self) -> List[VesselPosition]:
        """Get all vessels currently underway."""
        return [p for p in self.positions.values() if not p.at_dock and p.in_service]
