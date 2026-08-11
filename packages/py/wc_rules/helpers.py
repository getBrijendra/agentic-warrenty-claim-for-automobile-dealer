"""Deterministic domain helpers shared by the rules engine, the triage logic and the gateway simulator."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from wc_core.vocab import (CLAIM_TYPE_FOR_CONTRACT, CONTRACTS_FOR_CLAIM_TYPE, ELECTRICAL_SYSTEMS, HV_SYSTEMS,
                           MOD_AFFECTS, POWERTRAIN_SYSTEMS)

STOPWORDS = {"assembly", "assy", "kit", "unit", "module", "the", "and", "with", "front", "rear", "left", "right",
             "lh", "rh", "upper", "lower", "inner", "outer", "replacement", "genuine", "set", "of", "a"}


def as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def as_dt(v: Any) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def contract_valid(c: dict, on: date, km: int, system: str | None = None, check_scope: bool = False) -> tuple[bool, str]:
    start, end = as_date(c["start_date"]), as_date(c["end_date"])
    label = c["contract_type"]
    if on < start:
        return False, f"{label} starts {start.isoformat()}"
    if on > end:
        return False, f"{label} expired on {end.isoformat()}"
    if c.get("km_limit") is not None and km > int(c["km_limit"]):
        return False, f"odometer {km:,} km exceeds {label} limit {int(c['km_limit']):,} km"
    scope = c.get("component_scope") or []
    if check_scope and scope and system not in scope:
        return False, f"{system} is outside {label} scope ({', '.join(scope)})"
    km_txt = "unlimited km" if c.get("km_limit") is None else f"{km:,} / {int(c['km_limit']):,} km"
    return True, f"{label} valid until {end.isoformat()} ({km_txt})"


def select_coverage(contracts: list[dict], on: date, km: int, system: str) -> tuple[dict | None, str | None, list[str]]:
    """Pick the contract a claim should be filed under. COMPONENT terms win for in-scope systems, then BASE,
    then extended contracts. Returns (contract, claim_type, reasons)."""
    reasons: list[str] = []
    order = ["COMPONENT_BATTERY", "BASE", "EXTENDED", "PREMIUMCARE", "LIMITED_EXTENDED"]
    for ctype in order:
        for c in [c for c in contracts if c["contract_type"] == ctype]:
            component = ctype.startswith("COMPONENT")
            if component and system not in (c.get("component_scope") or []):
                continue
            ok, why = contract_valid(c, on, km, system, check_scope=component)
            reasons.append(why)
            if ok:
                return c, CLAIM_TYPE_FOR_CONTRACT[ctype], reasons
    return None, None, reasons


def contracts_for(claim_type: str, contracts: list[dict], contract_type: str | None) -> list[dict]:
    types = CONTRACTS_FOR_CLAIM_TYPE.get(claim_type, [])
    cs = [c for c in contracts if c["contract_type"] in types]
    if contract_type:
        cs.sort(key=lambda c: c["contract_type"] != contract_type)
    return cs


def matching_campaign(campaigns: list[dict], vin: str, ops: list[str]) -> dict | None:
    for cp in campaigns:
        if cp.get("status", "open") != "open":
            continue
        if vin in (cp.get("affected_vins") or []) and (not cp.get("labour_op") or cp["labour_op"] in ops):
            return cp
    return None


def find_duplicate(prior_claims: list[dict], vin: str, ops: list[str], closed_at: datetime, self_ref: str | None,
                   days: int = 7) -> dict | None:
    for pc in prior_claims:
        if pc.get("claim_ref") == self_ref or pc.get("status") in ("withdrawn",):
            continue
        if pc.get("vin") != vin:
            continue
        if not set(pc.get("labour_ops") or []) & set(ops):
            continue
        delta = abs((as_dt(pc["closed_at"]) - closed_at).total_seconds()) / 86400
        if delta <= days:
            return pc
    return None


def conflicting_mods(mods: list[str], system: str) -> list[str]:
    return [m for m in mods or [] if system in MOD_AFFECTS.get(m, set())]


def window_remaining_days(closed_at: datetime, now: datetime, window_days: int) -> float:
    return round(window_days - (now - closed_at).total_seconds() / 86400, 2)


def service_gaps(history: list[dict], start: date, before: date, interval_km: int, interval_months: int,
                 tolerance: float = 1.2) -> list[str]:
    """Return human-readable descriptions of missed periodic services."""
    events = sorted([h for h in history if h.get("kind") == "periodic_service" and as_date(h["service_date"]) <= before],
                    key=lambda h: as_date(h["service_date"]))
    gaps: list[str] = []
    prev_km, prev_date = 0, start
    for e in events:
        d, km = as_date(e["service_date"]), int(e["odometer_km"])
        if km - prev_km > interval_km * tolerance:
            gaps.append(f"{km - prev_km:,} km between services ({prev_date.isoformat()} → {d.isoformat()}); interval is {interval_km:,} km")
        elif (d - prev_date).days > interval_months * 30.5 * 1.5:
            gaps.append(f"{(d - prev_date).days} days between services ({prev_date.isoformat()} → {d.isoformat()})")
        prev_km, prev_date = km, d
    return gaps


def keyword(desc: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9/]+", desc.lower()) if w not in STOPWORDS and len(w) > 2]
    return words[0] if words else desc.lower()


def text_flags(system: str, texts: list[str]) -> dict[str, bool]:
    blob = " ".join(texts).upper()
    return {
        "is_electrical": system in ELECTRICAL_SYSTEMS,
        "is_powertrain": system in POWERTRAIN_SYSTEMS,
        "is_hv": system in HV_SYSTEMS,
        "is_leak": bool(re.search(r"\bLEAK|\bSEAL\b|SEEP", blob)),
    }


def next_weekday(after: datetime, weekday: int, hour: int = 10) -> datetime:
    d = after + timedelta(days=1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)
