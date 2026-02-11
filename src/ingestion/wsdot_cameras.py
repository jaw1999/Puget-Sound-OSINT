"""
WSDOT Camera Poller - Specialized handler for WSDOT ferry terminal cameras.

WSDOT cameras use a consistent URL pattern:
https://images.wsdot.wa.gov/wsf/{terminal}/terminal/{terminal}.jpg

Images refresh approximately every 30 seconds.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WSDOTTerminal:
    """WSDOT ferry terminal information."""
    name: str
    terminal_id: int
    url_slug: str  # Used in camera URL
    coordinates: tuple  # (lat, lon)

    @property
    def camera_url(self) -> str:
        return f"https://images.wsdot.wa.gov/wsf/{self.url_slug}/terminal/{self.url_slug}.jpg"


# Known WSDOT terminal configurations
WSDOT_TERMINALS: Dict[str, WSDOTTerminal] = {
    "anacortes": WSDOTTerminal("Anacortes", 1, "anacortes", (48.5074, -122.6793)),
    "bainbridge": WSDOTTerminal("Bainbridge Island", 3, "bainbridge", (47.6231, -122.5103)),
    "bremerton": WSDOTTerminal("Bremerton", 4, "bremerton", (47.5619, -122.6247)),
    "clinton": WSDOTTerminal("Clinton", 5, "clinton", (47.9750, -122.3519)),
    "coupeville": WSDOTTerminal("Coupeville", 11, "coupeville", (48.2225, -122.6785)),
    "edmonds": WSDOTTerminal("Edmonds", 8, "edmonds", (47.8137, -122.3838)),
    "fauntleroy": WSDOTTerminal("Fauntleroy", 9, "fauntleroy", (47.5226, -122.3928)),
    "fridayharbor": WSDOTTerminal("Friday Harbor", 10, "fridayharbor", (48.5357, -123.0159)),
    "kingston": WSDOTTerminal("Kingston", 12, "kingston", (47.7967, -122.4942)),
    "lopez": WSDOTTerminal("Lopez Island", 13, "lopez", (48.5706, -122.8880)),
    "mukilteo": WSDOTTerminal("Mukilteo", 14, "mukilteo", (47.9497, -122.3046)),
    "orcas": WSDOTTerminal("Orcas Island", 15, "orcas", (48.5975, -122.9440)),
    "pointdefiance": WSDOTTerminal("Point Defiance", 16, "pointdefiance", (47.3059, -122.5143)),
    "porttownsend": WSDOTTerminal("Port Townsend", 17, "porttownsend", (48.1126, -122.7604)),
    "seattle": WSDOTTerminal("Seattle", 7, "seattle", (47.6026, -122.3393)),
    "southworth": WSDOTTerminal("Southworth", 20, "southworth", (47.5131, -122.5006)),
    "tahlequah": WSDOTTerminal("Tahlequah", 21, "tahlequah", (47.3349, -122.5068)),
    "vashon": WSDOTTerminal("Vashon Island", 22, "vashon", (47.5083, -122.4635)),
}


class WSDOTCameraPoller:
    """
    Specialized poller for WSDOT ferry terminal cameras.

    Note: This is typically used through FeedManager, but can be used
    standalone for testing or simple use cases.
    """

    def __init__(self, terminals: Optional[List[str]] = None):
        """
        Initialize WSDOT camera poller.

        Args:
            terminals: List of terminal slugs to monitor (e.g., ["clinton", "seattle"])
                      If None, monitors all terminals.
        """
        if terminals:
            self.terminals = {k: v for k, v in WSDOT_TERMINALS.items() if k in terminals}
        else:
            self.terminals = WSDOT_TERMINALS.copy()

        logger.info(f"WSDOT poller initialized for {len(self.terminals)} terminals")

    def get_camera_urls(self) -> Dict[str, str]:
        """Get all camera URLs."""
        return {slug: t.camera_url for slug, t in self.terminals.items()}

    def get_terminal_info(self, slug: str) -> Optional[WSDOTTerminal]:
        """Get terminal information by slug."""
        return self.terminals.get(slug)

    @staticmethod
    def validate_url(url: str) -> bool:
        """Check if URL matches WSDOT camera pattern."""
        import re
        pattern = r"^https://images\.wsdot\.wa\.gov/wsf/[a-z]+/terminal/[a-z]+\.jpg$"
        return bool(re.match(pattern, url))

    @staticmethod
    def discover_terminals() -> List[str]:
        """
        Attempt to discover all working WSDOT terminal camera URLs.

        Returns list of working terminal slugs.
        """
        import requests

        working = []
        for slug, terminal in WSDOT_TERMINALS.items():
            try:
                resp = requests.head(terminal.camera_url, timeout=10)
                if resp.status_code == 200:
                    working.append(slug)
                    logger.debug(f"Terminal {slug}: OK")
                else:
                    logger.debug(f"Terminal {slug}: HTTP {resp.status_code}")
            except Exception as e:
                logger.debug(f"Terminal {slug}: {e}")

        logger.info(f"Discovered {len(working)}/{len(WSDOT_TERMINALS)} working terminals")
        return working
