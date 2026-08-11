"""Shared vocabulary: programmes, claim types, statuses, systems, rejection codes."""
from __future__ import annotations

from typing import Literal

Programme = Literal["FORD", "FORD_TRUCKS", "JLR", "FERRARI", "MASERATI", "VINFAST", "DEEPAL", "FOTON"]
ACTIVE_PROGRAMMES: list[str] = ["FORD", "FORD_TRUCKS", "JLR", "FERRARI", "MASERATI", "VINFAST"]
ALL_PROGRAMMES: list[str] = ACTIVE_PROGRAMMES + ["DEEPAL", "FOTON"]

CLAIM_PREFIX = {"FORD": "FD", "FORD_TRUCKS": "FT", "JLR": "JL", "FERRARI": "FE", "MASERATI": "MS",
                "VINFAST": "VF", "DEEPAL": "DP", "FOTON": "FO"}

ClaimType = Literal["BASE", "EXTENDED", "COMPONENT", "CAMPAIGN", "MAINTENANCE_PROGRAMME", "GOODWILL", "PARTS"]

ClaimStatus = Literal[
    "wip_closed", "triage", "building", "checking", "blocked", "ready", "submitted", "info_requested",
    "accepted", "rejected", "resolving", "resubmitted", "paid", "escalated", "written_off", "withdrawn",
]
TERMINAL_STATUSES = {"paid", "written_off", "withdrawn"}
WAITING_ON_HUMAN = {"escalated"}

# contract type ↔ claim type
CONTRACTS_FOR_CLAIM_TYPE: dict[str, list[str]] = {
    "BASE": ["BASE"],
    "EXTENDED": ["EXTENDED", "LIMITED_EXTENDED", "PREMIUMCARE"],
    "COMPONENT": ["COMPONENT_BATTERY"],
    "MAINTENANCE_PROGRAMME": ["MAINTENANCE_PROGRAMME"],
    "CAMPAIGN": [],
    "GOODWILL": [],
    "PARTS": [],
}
CLAIM_TYPE_FOR_CONTRACT = {
    "BASE": "BASE", "EXTENDED": "EXTENDED", "LIMITED_EXTENDED": "EXTENDED", "PREMIUMCARE": "EXTENDED",
    "COMPONENT_BATTERY": "COMPONENT", "MAINTENANCE_PROGRAMME": "MAINTENANCE_PROGRAMME",
}

ELECTRICAL_SYSTEMS = {"ELECTRICAL", "INFOTAINMENT", "PARKING_AID", "BATTERY_12V", "CHARGING", "HV_BATTERY"}
POWERTRAIN_SYSTEMS = {"ENGINE", "TURBO", "TRANSMISSION", "DRIVELINE"}
HV_SYSTEMS = {"HV_BATTERY", "CHARGING"}
DRIVELINE_SYSTEMS = {"ENGINE", "TRANSMISSION", "DRIVELINE"}

# aftermarket modification → systems it can plausibly affect
MOD_AFFECTS = {
    "lift_kit": {"SUSPENSION", "DRIVELINE", "STEERING"},
    "engine_remap": {"ENGINE", "TURBO", "TRANSMISSION"},
    "aftermarket_exhaust": {"ENGINE", "EMISSIONS"},
    "window_tint": set(),
    "aftermarket_head_unit": {"INFOTAINMENT", "ELECTRICAL"},
}

REJECTION_CODES = {
    "R01": ("Coverage expired (time/km) or not covered for this component", False),
    "R02": ("Outside submission window", False),
    "R03": ("Labour exceeds standard time", True),
    "R04": ("Missing or insufficient evidence", True),
    "R05": ("Narrative / 3C insufficient", True),
    "R06": ("Labour op or causal part mismatch", True),
    "R07": ("Duplicate claim", False),
    "R08": ("Pre-authorisation missing", False),
    "R09": ("Component outside extended-contract scope", False),
    "R10": ("Open campaign applies; claim as campaign", True),
    "R11": ("Maintenance non-compliance", False),
    "R12": ("Misuse / aftermarket modification", False),
    "R13": ("Failed part not retained or returned", False),
}
MANDATORY_ESCALATION_CODES = {"R01", "R02", "R09", "R11", "R12"}
