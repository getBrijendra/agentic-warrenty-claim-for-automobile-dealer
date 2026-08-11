"""Template text generation for the Fake LLM: technician-shorthand parsing, 3C drafting, status notes, drafts.
Wording variety comes from a seeded RNG so runs are reproducible."""
from __future__ import annotations

import random
import re

ABBREV = [
    (r"\bCUST\b", "customer"), (r"\bR&R\b", "removed and replaced"), (r"\bINOP\b", "inoperative"),
    (r"\bR/C\b", "recharged"), (r"\bPRESS\b", "pressure"), (r"\bASSY\b", "assembly"), (r"\bLH\b", "left-hand"),
    (r"\bRH\b", "right-hand"), (r"\bMSG\b", "message"), (r"\bTEMP\b", "temperature"), (r"\bPCT\b", "%"),
    (r"\bHRS\b", "hours"), (r"\bMIN\b", "min"), (r"\bECT\b", "coolant temperature"), (r"\bEVAC\b", "evacuated"),
    (r"\b2IN\b", "2-inch"), (r"\b3X\b", "3 times"), (r"\bDEF\b", "DEF"), (r"\bOBC\b", "OBC"), (r"\bIMC\b", "IMC"),
    (r"\bBMS\b", "BMS"), (r"\bSOH\b", "SOH"), (r"\bHV\b", "HV"), (r"\bDTC\b", "DTC"), (r"\bA/C\b", "A/C"),
    (r"\+", " and "),
]
KEEP_UPPER = {"DTC", "A/C", "HV", "SOH", "BMS", "OBC", "IMC", "DEF", "AC", "DC", "ECT", "CCA", "AGM", "TA", "PA",
              "RH", "LH", "R1234YF", "SE", "ROL", "OK", "F1"}

CAUSE_WORDS = ("INOP", "FAIL", "FAULT", "CRACK", "LEAK", "SEIZED", "NO CONTINUITY", "WORN", "IMBALANCE", "DELTA",
               "SPLIT", "ERRATIC", "DAMAGED", "CORRODED", "BLOCKED", "STRETCHED", "LOOSE", "DEBRIS", "NO RESPONSE",
               "INTERNAL", "ESCAPING", "NO PRESSURE", "DTC", "EXCESS")
FIX_WORDS = ("R&R", "REPLACE", "FITTED", "INSTALLED", "FLUSH", "R/C", "CALIBRAT", "BALANCE", "CONFIGURED", "SOFTWARE",
             "BLED", "TORQUED", "CLEANED", "TOPPED", "REGISTERED", "SELF-LEARN", "EVAC")
TEST_WORDS = ("TEST", "VERIFIED", "CLEARED", "VENT TEMP", "NO KNOCK", "NO LEAK", "NO DERATE", "NO DTC", "OK")
COMPLAINT_WORDS = ("CUST", "CUSTOMER", "DRIVER", "STATES", "REPORTS", "COMPLAINT")


def expand(s: str) -> str:
    out = s.strip().rstrip(".")
    for pat, rep in ABBREV:
        out = re.sub(pat, rep, out)
    words = []
    for w in out.split():
        core = re.sub(r"[^A-Za-z0-9/%-]", "", w)
        if core.upper() in KEEP_UPPER or re.match(r"^[A-Z]\d{3,4}", core) or re.match(r"^[A-Z]{2}-[A-Z]{2,3}-", core) \
                or re.search(r"\d", core):
            words.append(w)
        else:
            words.append(w.lower())
    txt = " ".join(words)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:1].upper() + txt[1:] if txt else txt


def sentences(story: str) -> list[str]:
    parts = re.split(r"(?<=[.])\s+|(?<=[.])$", story.strip())
    return [p.strip().rstrip(".") for p in parts if p and p.strip().rstrip(".")]


def draft_3c(story: str, complaint_verbatim: str) -> dict:
    sents = sentences(story)
    complaint_s, cause_s, fix_s, test_s = [], [], [], []
    for s in sents:
        u = s.upper()
        if any(u.startswith(w) or f" {w} " in f" {u} " for w in COMPLAINT_WORDS) and not cause_s:
            complaint_s.append(s)
        elif u.startswith("VERIFIED") or u.startswith("CONFIRMED"):
            rest = re.sub(r"^(VERIFIED|CONFIRMED)[,:]?\s*", "", s, flags=re.I)
            if rest and any(w in rest.upper() for w in CAUSE_WORDS):
                complaint_s.append("Verified")
                cause_s.append(rest)
            else:
                complaint_s.append(s)
        elif any(w in u for w in FIX_WORDS):
            fix_s.append(s)
        elif fix_s and re.search(r"\bNO (LEAK|KNOCK|DTC|DERATE|REPEAT)\b|TEST|\bOK\b|CLEARED|VENT TEMP", u):
            test_s.append(s)
        elif any(w in u for w in CAUSE_WORDS) or re.search(r"\d+\s?(BAR|MV|V|C|MM)\b", u):
            cause_s.append(s)
        elif any(w in u for w in TEST_WORDS) and (fix_s or cause_s):
            test_s.append(s)
        else:
            (cause_s if complaint_s else complaint_s).append(s)
    complaint = complaint_verbatim.rstrip(".") + "."
    extra = [expand(s) for s in complaint_s if not any(w in s.upper() for w in ("CUST STATES", "CUSTOMER STATES", "CUST REPORTS", "CUSTOMER REPORTS", "DRIVER REPORTS", "DRIVER STATES", "CUST COMPLAINT"))]
    if extra:
        complaint += " " + ". ".join(extra) + "."
    elif complaint_s:
        complaint += " Concern verified by technician."
    cause = ". ".join(expand(s) for s in cause_s)
    correction = ". ".join(expand(s) for s in fix_s + test_s)
    weak = len(story) < 60 or not cause_s or not fix_s or not (test_s or any("TEST" in s.upper() for s in fix_s))
    return {"complaint": complaint, "cause": (cause + ".") if cause else "", "correction": (correction + ".") if correction else "",
            "weak": weak}


def pick(rng: random.Random, options: list[str]) -> str:
    return options[rng.randrange(len(options))]


def money(v: float) -> str:
    return f"AED {v:,.0f}"
