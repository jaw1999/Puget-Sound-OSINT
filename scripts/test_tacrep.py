#!/usr/bin/env python3
"""
Test script for TACREP generation and ChatSurfer output.

Run from project root:
    python scripts/test_tacrep.py
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reporting.tacrep import TacrepGenerator, TacrepReport, ConfidenceLevel, parse_tacrep


def main():
    print("=" * 60)
    print("TACREP Generation Test")
    print("=" * 60)

    # Create generator with callsign
    gen = TacrepGenerator(callsign="PR01")

    # Generate check-in
    print("\n[CHECK-IN]")
    print(gen.generate_checkin())

    # Generate some reports
    print("\n[TACREP REPORTS]")

    # Report 1: Manual creation
    report1 = gen.create_report(
        num_targets=2,
        confidence=ConfidenceLevel.PROBABLE,
        platform="ORCA",
        tai="BALDER",
        remarks="ACTUAL RACCOON OFFLOADING",
        vessel_name="Tokitae",
        direction="INBOUND",
    )
    print(f"\nReport 1: {report1.to_tacrep_string()}")

    # Report 2: From detection dict
    detection = {
        "vessel_class": "Olympic",
        "vessel_name": "Samish",
        "confidence": 0.92,
        "direction": "outbound",
        "loading_state": "loading",
        "vehicle_count": 35,
    }
    report2 = gen.from_detection(
        detection=detection,
        tai="THOR",
        platform_mapping={"Olympic": "ORCA"}
    )
    print(f"\nReport 2: {report2.to_tacrep_string()}")

    # Report 3: Low confidence
    detection3 = {
        "vessel_class": "Unknown",
        "confidence": 0.45,
    }
    report3 = gen.from_detection(detection3, tai="BALDER")
    print(f"\nReport 3: {report3.to_tacrep_string()}")

    # Parse a TACREP string
    print("\n[PARSING]")
    test_str = "PR01//I005//2//PROBABLE//ORCA//BALDER//0211//REM: ACTUAL RACCOON OFFLOADING"
    parsed = parse_tacrep(test_str)
    print(f"Input:  {test_str}")
    print(f"Parsed: {parsed}")

    # Generate check-out
    print("\n[CHECK-OFF]")
    print(gen.generate_checkout())

    print("\n" + "=" * 60)
    print("Example TACREP Format:")
    print("CALLSIGN//SERIAL//# TARGETS//CONFIDENCE//PLATFORM//TAI//TIME(Z)//REM:")
    print("=" * 60)


if __name__ == "__main__":
    main()
