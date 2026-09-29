"""ResQNet Backend - Consolidated Single Backend File.
Contains all backend services: Configuration, State Management, LLM Wrappers,
Persistent Memory (Hindsight/Local), Agents (Incident, Verification, Resource, Volunteer, Notification),
CAP Alerts Parser & Fetcher, Guidance & Safety Protocols, Chatbot Engine, and Coordinator Orchestrator.
"""

import os
import sys
import copy
import json
import math
import re
import shutil
import pathlib
from pathlib import Path
import concurrent.futures
import datetime as dt
import email.utils
import xml.etree.ElementTree as ET
import base64
import requests
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# 1. CONFIGURATION & ENVIRONMENT
# -----------------------------------------------------------------------------
load_dotenv()
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "state.json"
SEED_FILE = DATA_DIR / "seed.json"
LOCAL_MEMORY_FILE = DATA_DIR / "local_memory.json"
OUTBOX_FILE = DATA_DIR / "outbox.jsonl"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_AUDIO_MODEL = os.getenv("GROQ_AUDIO_MODEL", "whisper-large-v3-turbo")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
HINDSIGHT_BASE_URL = os.getenv("HINDSIGHT_BASE_URL", "http://localhost:8888")
HINDSIGHT_API_KEY = os.getenv("HINDSIGHT_API_KEY", "")
HINDSIGHT_BANK_ID = os.getenv("HINDSIGHT_BANK_ID", "community-emergency")
IMD_CAP_FEED_URL = os.getenv("IMD_CAP_FEED_URL", "https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml")
RESQNET_DEMO_MODE = os.getenv("RESQNET_DEMO_MODE", "1").strip().lower() in ("1", "true", "yes")

INCIDENT_TYPES = [
    "flood", "cyclone", "heavy_rain", "heatwave", "earthquake", "tsunami", "wildfire",
    "landslide", "maritime_emergency", "abuse", "kidnapping", "sexual_assault", "child_labour",
    "water_problem", "medical_emergency", "road_blockage", "power_outage", "accident", "other"
]
SENSITIVE_INCIDENT_TYPES = {"abuse", "kidnapping", "sexual_assault", "child_labour"}

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from hindsight_client import Hindsight
except ImportError:
    Hindsight = None

# -----------------------------------------------------------------------------
# 2. STATE MANAGEMENT & SPATIAL UTILITIES
# -----------------------------------------------------------------------------
def load() -> dict:
    if not STATE_FILE.exists():
        shutil.copy(SEED_FILE, STATE_FILE)
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    for key in ("facilities", "saved_locations", "alert_history"):
        data.setdefault(key, copy.deepcopy(seed.get(key, [])))
    return data


def record_official_alerts(alerts: list[dict]) -> None:
    state = load()
    known = {item["id"] for item in state["alert_history"]}
    added = [item for item in alerts if item.get("is_live") and item.get("id") not in known]
    if added:
        state["alert_history"] = (state["alert_history"] + added)[-2500:]
        save(state)


def save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset() -> None:
    shutil.copy(SEED_FILE, STATE_FILE)


def coords(state: dict, place: str):
    return tuple(state["locations"].get((place or "").lower().strip(), (17.385, 78.4867)))


def haversine_km(a, b) -> float:
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(d))


# -----------------------------------------------------------------------------
# 3. LLM CLIENT WRAPPER
# -----------------------------------------------------------------------------
class LLM:
    """Thin Groq wrapper. If no key is configured, `available` is False and agents
    fall back to deterministic rules, so the system still runs offline."""

    def __init__(self):
        self.available = bool(GROQ_API_KEY and Groq)
        self.client = Groq(api_key=GROQ_API_KEY) if self.available else None

    def json(self, system: str, user: str, temperature: float = 0.1) -> dict:
        if not self.available:
            raise RuntimeError("Groq not configured")
        r = self.client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system + "\nRespond with a single valid JSON object only."},
                      {"role": "user", "content": user}],
        )
        txt = r.choices[0].message.content
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", txt, re.S)
            return json.loads(m.group(0))

    def text(self, system: str, user: str, temperature: float = 0.3) -> str:
        if not self.available:
            raise RuntimeError("Groq not configured")
        r = self.client.chat.completions.create(
            model=GROQ_MODEL, temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content.strip()


# -----------------------------------------------------------------------------
# 4. PERSISTENT MEMORY LAYER (HINDSIGHT / LOCAL)
# -----------------------------------------------------------------------------
class Memory:
    def __init__(self):
        self.bank = HINDSIGHT_BANK_ID
        self.client = None
        self.backend = "local"
        if Hindsight:
            try:
                kw = {"base_url": HINDSIGHT_BASE_URL}
                if HINDSIGHT_API_KEY:
                    kw["api_key"] = HINDSIGHT_API_KEY
                self.client = Hindsight(**kw)
                self.client.recall(bank_id=self.bank, query="ping")
                self.backend = "hindsight"
            except Exception as e:
                print(f"[memory] Hindsight unavailable ({type(e).__name__}); using local fallback.")
                self.client = None

    def _load_local(self):
        return json.loads(LOCAL_MEMORY_FILE.read_text(encoding="utf-8")) if LOCAL_MEMORY_FILE.exists() else []

    def _save_local(self, items):
        LOCAL_MEMORY_FILE.write_text(json.dumps(items, indent=1), encoding="utf-8")

    @staticmethod
    def _tok(s):
        return set(re.findall(r"[a-z0-9_]+", s.lower()))

    def retain(self, content: str, context: str = "emergency-coordination") -> None:
        stamp = dt.datetime.utcnow()
        if self.client:
            try:
                self.client.retain(bank_id=self.bank, content=content, context=context, timestamp=stamp)
                return
            except Exception as e:
                print(f"[memory] retain failed, saving locally: {e}")
        items = self._load_local()
        items.append({"content": content, "context": context, "ts": stamp.isoformat()})
        self._save_local(items)

    def recall(self, query: str, k: int = 5) -> list[str]:
        if self.client:
            try:
                res = self.client.recall(bank_id=self.bank, query=query)
                results = getattr(res, "results", res) or []
                return [getattr(r, "text", str(r)) for r in results][:k]
            except Exception as e:
                print(f"[memory] recall failed, using local: {e}")
        q = self._tok(query)
        scored = []
        for it in self._load_local():
            overlap = len(q & self._tok(it["content"]))
            if overlap:
                scored.append((overlap, it["ts"], it["content"]))
        scored.sort(reverse=True)
        return [c for _, _, c in scored[:k]]

    def reflect(self, question: str) -> str:
        if self.client:
            try:
                res = self.client.reflect(bank_id=self.bank, query=question)
                return getattr(res, "text", str(res))
            except Exception as e:
                print(f"[memory] reflect failed: {e}")
        hits = self.recall(question, k=6)
        return "Relevant past records:\n- " + "\n- ".join(hits) if hits else "No relevant history yet."


# -----------------------------------------------------------------------------
# 5. AGENTS
# -----------------------------------------------------------------------------
KEYWORDS = {
    "flood": ["flood", "waterlogg", "submerg", "overflow", "inundat"],
    "cyclone": ["cyclone", "storm surge", "cyclonic"],
    "maritime_emergency": ["ship in distress", "ship sinking", "vessel in distress", "man overboard", "boat capsized", "ship emergency", "maritime"],
    "kidnapping": ["kidnap", "abduct", "taken by force", "missing child"],
    "sexual_assault": ["rape", "sexual assault", "molest", "sexual violence", "sexually assaulted"],
    "child_labour": ["child labour", "child labor", "forced child work", "child being made to work"],
    "abuse": ["domestic violence", "physical abuse", "child abuse", "being beaten", "abuse at home"],
    "water_problem": ["water supply", "contaminated water", "water contamination", "no drinking water", "water pipeline leak"],
    "heavy_rain": ["heavy rain", "rainfall", "downpour", "cloudburst", "thunderstorm"],
    "heatwave": ["heat wave", "heatwave", "heat stroke", "extreme heat"],
    "earthquake": ["earthquake", "seismic", "tremor"],
    "tsunami": ["tsunami"],
    "wildfire": ["wildfire", "forest fire"],
    "landslide": ["landslide", "landslip"],
    "accident": ["road accident", "collision", "vehicle crash", "car crash", "bus crash"],
    "medical_emergency": ["injur", "unconscious", "heart", "accident", "bleeding", "pregnan", "ambulance", "medical", "collapsed"],
    "road_blockage": ["blocked", "blockage", "tree fell", "landslide", "road closed", "debris"],
    "power_outage": ["power cut", "power outage", "no electricity", "blackout", "transformer", "no power", "power failure"],
}

DEFAULT_NEEDS = {
    "flood": ["boat", "pump", "first_aid", "tarpaulin"],
    "cyclone": ["boat", "pump", "first_aid", "tarpaulin"],
    "maritime_emergency": ["boat", "ambulance", "first_aid"],
    "abuse": ["ambulance", "first_aid"],
    "kidnapping": ["ambulance", "first_aid"],
    "sexual_assault": ["ambulance", "first_aid"],
    "child_labour": ["ambulance", "first_aid"],
    "water_problem": ["pump", "first_aid"],
    "heavy_rain": ["pump", "tarpaulin", "first_aid"],
    "heatwave": ["ambulance", "first_aid"],
    "earthquake": ["jcb", "first_aid", "ambulance", "tarpaulin"],
    "tsunami": ["boat", "ambulance", "first_aid"],
    "wildfire": ["jcb", "ambulance", "first_aid"],
    "landslide": ["jcb", "ambulance", "first_aid"],
    "medical_emergency": ["ambulance", "first_aid"],
    "accident": ["ambulance", "first_aid", "traffic_police"],
    "road_blockage": ["jcb", "traffic_police"],
    "power_outage": ["generator", "electrician"],
    "other": ["first_aid"],
}

SKILLS_BY_TYPE = {
    "flood": ["swimming", "boat_operation", "rescue", "shelter_management"],
    "cyclone": ["rescue", "boat_operation", "first_aid", "shelter_management"],
    "maritime_emergency": ["swimming", "boat_operation", "rescue", "first_aid"],
    "abuse": ["medical", "nursing", "first_aid"],
    "kidnapping": ["rescue", "communication", "first_aid"],
    "sexual_assault": ["medical", "nursing", "first_aid"],
    "child_labour": ["communication", "first_aid"],
    "water_problem": ["logistics", "communication"],
    "heavy_rain": ["logistics", "shelter_management"],
    "heatwave": ["medical", "doctor", "nursing", "first_aid"],
    "earthquake": ["rescue", "heavy_vehicle", "first_aid", "logistics"],
    "tsunami": ["swimming", "boat_operation", "rescue", "first_aid"],
    "wildfire": ["rescue", "logistics", "first_aid"],
    "landslide": ["heavy_vehicle", "rescue", "logistics", "first_aid"],
    "medical_emergency": ["medical", "doctor", "nursing", "first_aid"],
    "accident": ["medical", "doctor", "nursing", "first_aid", "traffic_control"],
    "road_blockage": ["traffic_control", "heavy_vehicle", "logistics"],
    "power_outage": ["electrician", "generator", "communication"],
    "other": ["communication", "first_aid"],
}


class IncidentAgent:
    """Turns a free-text citizen report into a structured incident."""
    name = "IncidentAgent"

    def __init__(self, llm, memory):
        self.llm, self.memory = llm, memory

    def run(self, report: str, state: dict, location_context: dict | None = None) -> dict:
        context = location_context or {}
        selected_type = context.get("hazard_type")
        report_type = next((kind for kind, terms in KEYWORDS.items()
                            if kind in SENSITIVE_INCIDENT_TYPES
                            and any(term in report.casefold() for term in terms)), None)
        sensitive_type = selected_type if selected_type in SENSITIVE_INCIDENT_TYPES else report_type
        past = [] if sensitive_type else self.memory.recall(report, k=3)
        places = list(state["locations"].keys())
        if sensitive_type:
            d = self._rules(report, places)
            d["type"] = sensitive_type
        else:
            try:
                d = self.llm.json(
                    "You are an emergency intake analyst for India. Extract a structured incident. "
                    f"type must be one of {INCIDENT_TYPES}. severity is an int 1 (minor) to 5 (life-threatening). "
                    f"location must be the closest match from this list, or null: {places}. "
                    "Fields: type, severity, location, people_affected (int estimate), summary (<=25 words), "
                    "urgent_needs (list of short strings). Use past incidents only as context.",
                    f"Report: {report}\nSimilar past incidents: {past}")
            except Exception:
                d = self._rules(report, places)
        d["type"] = d.get("type") if d.get("type") in INCIDENT_TYPES else "other"
        d["severity"] = max(1, min(5, int(d.get("severity", 3))))
        if context.get("hazard_type") in INCIDENT_TYPES:
            d["type"] = context["hazard_type"]
        if d["type"] in SENSITIVE_INCIDENT_TYPES:
            d["summary"] = "Sensitive incident report; details withheld for confidential review."
        if context:
            d["state"] = context.get("state", "")
            d["district"] = context.get("district", "")
            d["location"] = context.get("location") or ", ".join(filter(None, [d["district"], d["state"]]))
            d["coords"] = context.get("coordinates") or coords(state, d["location"])
        else:
            if d.get("location") not in state["locations"]:
                d["location"] = next((p for p in places if p in report.lower()), "secunderabad")
            d["state"] = "Telangana"
            d["district"] = d["location"].title()
            d["coords"] = coords(state, d["location"])
        d["raw_report"] = report
        d["past_context"] = past
        return d

    @staticmethod
    def _rules(report, places):
        r = report.lower()
        itype = next((t for t, kws in KEYWORDS.items() if any(k in r for k in kws)), "other")
        sev = 3
        if any(w in r for w in ["trapped", "unconscious", "drown", "children", "elderly", "bleeding", "critical",
                    "distress", "sinking", "overboard", "capsized"]): sev = 5
        elif any(w in r for w in ["injur", "submerg", "hours"]): sev = 4
        m = re.search(r"(\d+)\s*(people|persons|families|residents)", r)
        return {"type": itype, "severity": sev, "location": next((p for p in places if p in r), None),
                "people_affected": int(m.group(1)) if m else 5, "summary": report[:120], "urgent_needs": []}


class VerificationAgent:
    """Cross-checks a new incident against the live registry + long-term memory."""
    name = "VerificationAgent"

    def __init__(self, llm, memory):
        self.llm, self.memory = llm, memory

    def run(self, inc: dict, state: dict, official_alerts: list[dict] | None = None) -> dict:
        if inc["type"] in SENSITIVE_INCIDENT_TYPES:
            return {"verified": False, "confidence": 0.0, "duplicate_of": None,
                    "corroborated_by": [], "needs_human_check": True,
                    "reasons": ["Sensitive report bypasses AI and shared channels; contact a trained responder or helpline directly."],
                    "history": [], "official_alert_ids": [], "preposition_recommendation": ""}
        now = dt.datetime.utcnow()
        nearby, dup = [], None
        for old in state["incidents"]:
            if old["status"] == "closed":
                continue
            age_h = (now - dt.datetime.fromisoformat(old["created_at"])).total_seconds() / 3600
            dist = haversine_km(inc["coords"], old["coords"])
            if dist <= 3 and age_h <= 6 and old["type"] == inc["type"]:
                nearby.append(old["id"])
                if dist <= 0.5 and age_h <= 2:
                    dup = old["id"]
        history = self.memory.recall(f"{inc['type']} at {inc['location']} resolved history", k=4)
        matching_alerts = []
        aliases = {"heavy_rain": "heavy rain", "medical_emergency": "medical emergency", "road_blockage": "road blockage", "power_outage": "power outage"}
        incident_hazard = aliases.get(inc["type"], inc["type"]).casefold()
        related_hazards = {"flood": {"heavy rain"}, "landslide": {"heavy rain"}, "cyclone": {"heavy rain"}}
        for alert in official_alerts or []:
            if not alert.get("is_live") or alert.get("source") != "Official":
                continue
            if alert.get("status", "Active") != "Active":
                continue
            same_state = not inc.get("state") or not alert.get("state") or inc["state"].casefold() == alert["state"].casefold()
            area = f"{alert.get('area', '')} {alert.get('district', '')}".casefold()
            district = inc.get("district", "").casefold()
            same_area = not district or district in area or district == alert.get("state", "").casefold()
            hazard = alert.get("hazard", "").casefold()
            same_hazard = incident_hazard in hazard or hazard in incident_hazard or hazard in related_hazards.get(incident_hazard, set())
            if same_state and same_area and same_hazard:
                matching_alerts.append(alert)
        confidence = min(0.95, 0.45 + 0.15 * len(nearby) + (0.1 if history else 0) + 0.05 * inc["severity"]
                         + min(0.25, 0.15 * len(matching_alerts)))
        reasons = [f"{len(nearby)} corroborating open report(s) within 3 km" if nearby else "no corroborating reports yet",
                   f"{len(history)} related historical record(s) in memory"]
        if matching_alerts:
            reasons.append(f"{len(matching_alerts)} active official alert(s) match the hazard and area")
        try:
            v = self.llm.json(
                "You verify emergency reports and detect hoaxes/panic. Return: credibility (0-1 float), "
                "reasoning (<=30 words), needs_human_check (bool).",
                f"Incident: {inc['summary']} | type={inc['type']} sev={inc['severity']} loc={inc['location']}\n"
                f"Corroborating open incidents: {nearby}\nHistory: {history}")
            confidence = round((confidence + float(v.get("credibility", confidence))) / 2, 2)
            reasons.append(v.get("reasoning", ""))
            human = bool(v.get("needs_human_check", False))
        except Exception:
            human = confidence < 0.55 and inc["severity"] >= 4
        verified = confidence >= 0.5 or inc["severity"] >= 4 or bool(matching_alerts)
        if matching_alerts:
            confidence = max(confidence, 0.65)
            inc["severity"] = min(5, inc["severity"] + 1)
        strongest = next((item for item in matching_alerts if item.get("color") in ("Red", "Orange")), None)
        return {"verified": verified, "confidence": round(confidence, 2), "duplicate_of": dup,
                "corroborated_by": nearby, "needs_human_check": human,
                "reasons": [r for r in reasons if r], "history": history,
                "official_alert_ids": [item["id"] for item in matching_alerts],
                "preposition_recommendation": (f"Pre-position {inc['type'].replace('_', ' ')} response resources in {inc['location']}; "
                                f"active {strongest['color'].lower()} official warning overlaps local reports."
                                if strongest else "")}


class ResourceAgent:
    """Chooses resource kinds and allocates the nearest available units."""
    name = "ResourceAgent"

    def __init__(self, llm, memory):
        self.llm, self.memory = llm, memory

    def run(self, inc: dict, ver: dict, state: dict) -> dict:
        kinds = sorted({r["kind"] for r in state["resources"]})
        needs = DEFAULT_NEEDS[inc["type"]]
        past = self.memory.recall(f"resources that worked for {inc['type']} in {inc['location']}", k=4)
        try:
            d = self.llm.json(
                f"Pick emergency resources from this list only: {kinds}. Return {{\"kinds\": [..], \"reason\": str}}. "
                "Prefer what worked in past resolutions.",
                f"Incident: {inc['summary']} type={inc['type']} severity={inc['severity']} people={inc.get('people_affected')}\n"
                f"Past resolutions: {past}")
            selected = [k for k in d.get("kinds", []) if k in kinds]
            if inc["type"] == "maritime_emergency":
                selected = DEFAULT_NEEDS[inc["type"]] + selected
            needs = list(dict.fromkeys(selected)) or needs
        except Exception:
            pass
        qty = 1 if inc["severity"] <= 3 else 2
        allocated, shortages = [], []
        for kind in needs:
            cands = sorted((r for r in state["resources"] if r["kind"] == kind and r["available"] > 0),
                           key=lambda r: haversine_km(inc["coords"], coords(state, r["location"])))
            if not cands:
                shortages.append(kind)
                continue
            r = cands[0]
            take = min(qty, r["available"])
            r["available"] -= take
            allocated.append({"resource_id": r["id"], "kind": kind, "name": r["name"], "qty": take, "from": r["location"],
                              "distance_km": round(haversine_km(inc["coords"], coords(state, r["location"])), 1)})
        return {"allocated": allocated, "shortages": shortages}


class VolunteerAgent:
    """Ranks volunteers by skill match, proximity, availability and past track record."""
    name = "VolunteerAgent"

    def __init__(self, llm, memory):
        self.llm, self.memory = llm, memory

    def run(self, inc: dict, state: dict) -> dict:
        wanted = set(SKILLS_BY_TYPE[inc["type"]])
        want_n = 1 if inc["severity"] <= 2 else 2 if inc["severity"] <= 3 else 3
        scored = []
        for v in state["volunteers"]:
            if not v["available"]:
                continue
            match = len(wanted & set(v["skills"]))
            if not match:
                continue
            dist = haversine_km(inc["coords"], coords(state, v["location"]))
            scored.append((match * 10 - dist + min(v.get("completed", 0), 10) * 0.5, dist, v))
        scored.sort(key=lambda x: -x[0])
        chosen = []
        for _, dist, v in scored[:want_n]:
            v["available"] = False
            chosen.append({"volunteer_id": v["id"], "name": v["name"], "phone": v["phone"],
                           "matched_skills": sorted(wanted & set(v["skills"])), "distance_km": round(dist, 1),
                           "languages": v["languages"]})
        return {"assigned": chosen, "unfilled": max(0, want_n - len(chosen))}


class NotificationAgent:
    """Drafts and dispatches multilingual alerts."""
    name = "NotificationAgent"

    def __init__(self, llm, memory):
        self.llm, self.memory = llm, memory

    def run(self, inc: dict, ver: dict, res: dict, vol: dict) -> list[dict]:
        base = (f"{inc['type'].replace('_', ' ').title()} reported at {inc['location'].title()} "
                f"(severity {inc['severity']}/5).")
        resources = ", ".join(f"{a['qty']}x {a['name']}" for a in res["allocated"]) or "none available"
        try:
            m = self.llm.json(
                "Write concise emergency alerts (<=40 words each). Return keys: citizens_en, citizens_te (Telugu), "
                "citizens_hi (Hindi), volunteers, authorities. Include safety advice for citizens; no invented facts.",
                f"Incident: {base} {inc['summary']}\nResources dispatched: {resources}\n"
                f"Volunteers: {[v['name'] for v in vol['assigned']]}\nShortages: {res['shortages']}\n"
                f"Needs human check: {ver['needs_human_check']}")
        except Exception:
            m = {"citizens_en": f"ALERT: {base} Avoid the area and follow official instructions. Help is on the way.",
                 "volunteers": f"TASK: {base} {inc['summary']} Report to the location immediately.",
                 "authorities": f"{base} Resources: {resources}. Shortages: {res['shortages'] or 'none'}. "
                                f"Human check: {ver['needs_human_check']}"}
        out = [self._deliver("community-broadcast", k, t, inc) for k, t in m.items() if k.startswith("citizens")]
        for v in vol["assigned"]:
            out.append(self._deliver(v["phone"], "volunteer", f"{v['name']}: {m.get('volunteers', '')}", inc))
        out.append(self._deliver("emergency-control-room", "authorities", m.get("authorities", ""), inc))
        return out

    def _deliver(self, to, channel, text, inc):
        rec = {"ts": dt.datetime.utcnow().isoformat(), "incident": inc.get("id"), "to": to, "channel": channel, "text": text}
        with open(OUTBOX_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


# -----------------------------------------------------------------------------
# 6. CAP ALERTS & FEEDS (IMD & WEATHER WARNINGS)
# -----------------------------------------------------------------------------
DEFAULT_FEED_URL = "https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml"
OFFICIAL_HOST = "cap-sources.s3.amazonaws.com"
INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal", "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]
ALERT_HAZARD_TERMS = {
    "flood": ("flood", "inundat", "flash flood"),
    "cyclone": ("cyclone", "cyclonic", "storm surge"),
    "maritime_emergency": ("ship in distress", "ship sinking", "vessel in distress", "man overboard", "boat capsized", "maritime emergency"),
    "heavy rain": ("rain", "rainfall", "downpour", "thunderstorm"),
    "heatwave": ("heat wave", "heatwave", "hot weather"),
    "earthquake": ("earthquake", "seismic"),
    "tsunami": ("tsunami",),
    "wildfire": ("wildfire", "forest fire"),
    "landslide": ("landslide", "landslip"),
}
HAZARD_TERMS = ALERT_HAZARD_TERMS
SEVERITY_COLORS = {"extreme": "Red", "severe": "Orange", "moderate": "Yellow", "minor": "Green", "unknown": "Yellow"}


class AlertFeedError(RuntimeError):
    pass


def _local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element, name, default=""):
    for child in element:
        if _local_name(child.tag) == name.lower():
            return (child.text or "").strip()
    return default


def _descendant(element, name):
    return next((item for item in element.iter() if _local_name(item.tag) == name.lower()), None)


def _descendant_text(element, name, default=""):
    item = _descendant(element, name)
    return (item.text or "").strip() if item is not None else default


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            return None


def _hazard(text):
    normalized = text.casefold()
    for hazard, terms in ALERT_HAZARD_TERMS.items():
        if any(term in normalized for term in terms):
            return hazard
    return "other"


def _state(area):
    normalized = area.casefold()
    return next((state for state in INDIAN_STATES if state.casefold() in normalized), "")


def _centroid(info):
    area = next((item for item in info if _local_name(item.tag) == "area"), None)
    if area is None:
        return None
    polygon = next((item.text for item in area if _local_name(item.tag) == "polygon" and item.text), None)
    if polygon:
        coords_list = []
        for pair in polygon.split():
            try:
                latitude, longitude = map(float, pair.split(",", 1))
                coords_list.append((latitude, longitude))
            except (ValueError, TypeError):
                continue
        if coords_list:
            return [sum(point[0] for point in coords_list) / len(coords_list), sum(point[1] for point in coords_list) / len(coords_list)]
    return None


def parse_cap_alert(xml_text, now=None):
    root = ET.fromstring(xml_text)
    status = _child_text(root, "status", "Actual")
    scope = _child_text(root, "scope", "Public")
    if status.casefold() != "actual" or scope.casefold() != "public":
        return None
    info_blocks = [item for item in root if _local_name(item.tag) == "info"]
    if not info_blocks:
        return None
    info = info_blocks[0]
    title = _child_text(info, "headline") or _child_text(info, "event") or "Official weather warning"
    description = _child_text(info, "description") or title
    severity = _child_text(info, "severity", "Unknown").casefold()
    effective = _parse_time(_child_text(info, "effective") or _child_text(info, "onset") or _child_text(root, "sent"))
    expires = _parse_time(_child_text(info, "expires"))
    if not expires and effective:
        expires = effective + dt.timedelta(hours=6)
    current = now or dt.datetime.now(dt.timezone.utc)
    if expires and expires <= current:
        return None
    if effective and effective > current:
        active_status = "Upcoming"
    else:
        active_status = "Active"
    area = next((item for item in info if _local_name(item.tag) == "area"), None)
    area_description = _child_text(area, "areaDesc", "All India") if area is not None else "All India"
    district = ""
    if area is not None:
        for geocode in (item for item in area if _local_name(item.tag) == "geocode"):
            label = _child_text(geocode, "valueName").casefold()
            if "district" in label:
                district = _child_text(geocode, "value")
                break
    sender = _child_text(info, "senderName") or _child_text(root, "sender") or "India Meteorological Department"
    instruction = _child_text(info, "instruction")
    polygon = _centroid(info)
    link = _child_text(info, "web")
    return {
        "id": _child_text(root, "identifier") or title,
        "title": title,
        "summary": " ".join(description.split())[:400],
        "hazard": _hazard(f"{title} {description}"),
        "severity": severity.title(),
        "color": SEVERITY_COLORS.get(severity, "Green"),
        "state": _state(area_description),
        "district": district,
        "area": area_description,
        "source": "Official",
        "source_detail": "India Meteorological Department · CAP feed",
        "authority": sender,
        "status": active_status,
        "sent_at": _child_text(root, "sent"),
        "effective": effective.isoformat() if effective else "",
        "expires": expires.isoformat() if expires else "",
        "instruction": instruction,
        "coordinates": polygon,
        "url": link,
        "is_live": True,
    }


def parse_rss_items(xml_text):
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter():
        if _local_name(item.tag) != "item":
            continue
        link = _child_text(item, "link")
        if link.startswith(f"https://{OFFICIAL_HOST}/"):
            items.append({"title": _child_text(item, "title"), "summary": _child_text(item, "description"), "url": link})
    return items


def fetch_official_alerts(feed_url=DEFAULT_FEED_URL, limit=20, now=None):
    try:
        response = requests.get(feed_url, timeout=10, headers={"User-Agent": "ResQNet/1.0"})
        response.raise_for_status()
        items = parse_rss_items(response.content)
    except (requests.RequestException, ET.ParseError, ValueError) as error:
        raise AlertFeedError(f"IMD alert feed unavailable: {error}") from error
    if not items:
        return []

    def fetch(item):
        try:
            detail = requests.get(item["url"], timeout=8, headers={"User-Agent": "ResQNet/1.0"})
            detail.raise_for_status()
            alert = parse_cap_alert(detail.content, now=now)
            return alert
        except (requests.RequestException, ET.ParseError, ValueError):
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        alerts = [alert for alert in executor.map(fetch, items[:max(1, limit)]) if alert]
    return sorted(alerts, key=lambda item: item["sent_at"], reverse=True)


def demo_alerts(now=None):
    current = now or dt.datetime.now(dt.timezone.utc)
    expires = (current + dt.timedelta(hours=6)).isoformat()
    return [{
        "id": "DEMO-IMD-TG-001", "title": "Heavy rain watch · Hyderabad", "summary": "Heavy rain may cause waterlogging in low-lying areas. Avoid underpasses and follow local authority instructions.",
        "hazard": "heavy rain", "severity": "Moderate", "color": "Yellow", "state": "Telangana", "district": "Hyderabad",
        "area": "Hyderabad, Telangana", "source": "DEMO SIMULATED", "source_detail": "SIMULATED IMD-style demo data",
        "authority": "Demo data · not an official warning", "status": "Active", "sent_at": current.isoformat(),
        "effective": current.isoformat(), "expires": expires, "instruction": "Avoid low-lying areas and underpasses.",
        "coordinates": [17.385, 78.4867], "url": "", "is_live": False,
    }]


def area_risk_scores(alerts, alert_history, incidents):
    areas = {}

    def get_area(st_name, district):
        key = (st_name or "Unknown", district or "Area not identified")
        return areas.setdefault(key, {"state": key[0], "district": key[1], "official_alerts": 0,
                                      "citizen_reports": 0, "history_signals": 0, "score": 0})

    alert_weights = {"Red": 45, "Orange": 32, "Yellow": 18, "Green": 5}
    seen_ids = set()
    for alert in alerts:
        if alert.get("source") != "Official" or not alert.get("is_live"):
            continue
        row = get_area(alert.get("state"), alert.get("district") or alert.get("area"))
        row["official_alerts"] += 1
        row["score"] += alert_weights.get(alert.get("color"), 0)
        seen_ids.add(alert.get("id"))
    for alert in alert_history:
        if alert.get("id") in seen_ids or alert.get("source") != "Official":
            continue
        row = get_area(alert.get("state"), alert.get("district") or alert.get("area"))
        row["history_signals"] += 1
        row["score"] += 4
    for incident in incidents:
        row = get_area(incident.get("state"), incident.get("district") or incident.get("location"))
        reports = max(1, int(incident.get("report_count", 1)))
        row["citizen_reports"] += reports
        multiplier = 6 if incident.get("status") != "closed" else 2
        row["score"] += min(20, reports * 3) + max(1, int(incident.get("severity", 1))) * multiplier
    result = []
    for row in areas.values():
        score = min(100, row.pop("score"))
        row["risk_score"] = score
        row["risk"] = "High" if score >= 60 else "Moderate" if score >= 25 else "Low"
        result.append(row)
    return sorted(result, key=lambda item: item["risk_score"], reverse=True)


def matches_saved_location(alert, place, radius_km=75):
    if alert.get("state") and place.get("state") and alert["state"].casefold() != place["state"].casefold():
        return False
    district = place.get("district", "").casefold()
    area = alert.get("area", "").casefold()
    if district and (district in area or district == alert.get("district", "").casefold()):
        return True
    if alert.get("coordinates") and place.get("coordinates"):
        if haversine_km(alert["coordinates"], place["coordinates"]) <= radius_km:
            return True
    return bool(alert.get("state") and place.get("state") and alert["state"].casefold() == place["state"].casefold()
                and area.strip() in (alert["state"].casefold(), f"all {alert['state'].casefold()}"))


# -----------------------------------------------------------------------------
# 7. GUIDANCE & HELPLINES
# -----------------------------------------------------------------------------
HELPLINE = {"number": "112", "label": "India Emergency Response Support System", "source": "Government of India"}
SUPPORT_CONTACTS = {
    "112": {"label": "Emergency response", "number": "112", "url": "https://112.gov.in/"},
    "181": {"label": "Women Helpline (availability varies by state)", "number": "181", "url": "https://wcd.gov.in/schemes/women-helpline-scheme"},
    "1098": {"label": "Child Helpline", "number": "1098", "url": "https://childlineindia.org.in/"},
    "108": {"label": "Ambulance (availability varies by state)", "number": "108", "url": "https://nhm.gov.in/"},
    "1078": {"label": "Disaster management control room (confirm local availability)", "number": "1078", "url": "https://ndma.gov.in/"},
}

SUPPORT_CONTACTS_BY_TYPE = {
    "abuse": ["181", "112", "1098"],
    "kidnapping": ["112", "1098"],
    "sexual_assault": ["181", "112", "1098"],
    "child_labour": ["1098", "112"],
    "accident": ["108", "112"],
    "medical_emergency": ["108", "112"],
    "flood": ["1078", "112"],
    "heavy_rain": ["1078", "112"],
    "cyclone": ["1078", "112"],
    "earthquake": ["1078", "112"],
    "tsunami": ["1078", "112"],
    "wildfire": ["1078", "112"],
    "landslide": ["1078", "112"],
    "maritime_emergency": ["112"],
}
PRIMARY_CONTACT_BY_TYPE = {
    "abuse": "181", "kidnapping": "112", "sexual_assault": "181", "child_labour": "1098",
    "accident": "108", "medical_emergency": "108", "flood": "1078", "heavy_rain": "1078",
    "cyclone": "1078", "earthquake": "1078", "tsunami": "1078", "wildfire": "1078",
    "landslide": "1078", "maritime_emergency": "112",
}

NEARBY_HELP_SEARCHES = {
    "abuse": ["police station", "One Stop Centre Sakhi", "hospital"],
    "kidnapping": ["police station", "child welfare committee", "hospital"],
    "sexual_assault": ["One Stop Centre Sakhi", "government hospital", "police station"],
    "child_labour": ["child welfare committee", "police station", "labour department office"],
    "accident": ["hospital", "police station", "ambulance"],
    "medical_emergency": ["hospital", "emergency room", "ambulance"],
    "flood": ["emergency shelter", "hospital", "fire station"],
    "earthquake": ["emergency shelter", "hospital", "fire station"],
    "maritime_emergency": ["coast guard station", "police station", "hospital"],
    "water_problem": ["municipal water supply office", "water testing laboratory"],
}

GUIDANCE = {
    "flood": {
        "before": "Move documents and medicines above expected water level; identify an official shelter and evacuation route.",
        "during": "Move to higher ground. Never walk or drive through floodwater; avoid power lines and switch off electricity only if safe.",
        "after": "Return only when authorities say it is safe. Avoid floodwater, boil drinking water, and photograph damage for assistance.",
    },
    "cyclone": {
        "before": "Follow IMD and local authority bulletins, secure loose objects, charge phones, and know your nearest cyclone shelter.",
        "during": "Stay indoors away from windows. Do not go outside during the eye; wait for an official all-clear.",
        "after": "Avoid damaged buildings, fallen wires, and floodwater. Use official updates before travelling.",
    },
    "heavy rain": {
        "before": "Check local warnings, clear safe drainage, and plan a route that avoids underpasses and low-lying roads.",
        "during": "Stay away from swollen drains and streams. Do not cross flooded roads; move to a safe building if water rises.",
        "after": "Check for electrical damage and contaminated water. Follow local authority advisories before returning.",
    },
    "heatwave": {
        "before": "Plan outdoor work for cooler hours, keep water available, and check on older adults and people with illness.",
        "during": "Move to shade or a cool place, sip water, and seek medical help for confusion, fainting, or very hot skin.",
        "after": "Recover in a cool place and seek care if symptoms persist. Follow local health advisories.",
    },
    "earthquake": {
        "before": "Secure heavy furniture, identify safe cover, and keep shoes, a torch, and essential medicines accessible.",
        "during": "Drop, cover, and hold on. Stay away from windows; if outdoors, move away from buildings and wires.",
        "after": "Expect aftershocks. Avoid damaged structures and lifts; check for injuries and follow official evacuation instructions.",
    },
    "tsunami": {
        "before": "Know the nearest high ground and follow official coastal evacuation alerts immediately.",
        "during": "Move inland or to high ground on foot. Do not go to the shore to watch the sea.",
        "after": "Stay away from the coast until authorities issue an all-clear; multiple waves may arrive.",
    },
    "wildfire": {
        "before": "Keep an evacuation route clear, prepare medicines and documents, and follow forest or district advisories.",
        "during": "Evacuate when instructed. Avoid smoke, close windows if sheltering, and call 112 for immediate danger.",
        "after": "Return only after an official all-clear. Avoid hot ash, unstable trees, and damaged power lines.",
    },
    "landslide": {
        "before": "Watch for official warnings and cracks, leaning trees, or unusual water flow near slopes; plan an uphill route.",
        "during": "Move away from the slide path and river channels; do not cross fresh debris.",
        "after": "Stay clear of the slope and report blocked roads or trapped people to authorities.",
    },
    "accident": {
        "before": "If safe, stop at a distance, switch on hazard lights, and call 112 with the exact location and injuries.",
        "during": "Do not move seriously injured people unless there is immediate danger. Keep traffic clear and follow dispatcher instructions.",
        "after": "Share verified updates with responders. Do not crowd the scene or post victims’ identifying images publicly.",
    },
    "medical emergency": {
        "before": "Call 112 for immediate danger and share the person's location, symptoms, and known medical needs.",
        "during": "Follow the emergency operator's instructions. Do not give food, drink, or medication unless a qualified professional advises it.",
        "after": "Keep the route clear for responders and share relevant medical information with the care team.",
    },
    "road blockage": {
        "before": "Check official traffic updates and plan an alternate route before approaching the blocked road.",
        "during": "Do not enter unstable debris or flooded road sections. Keep a safe distance and report trapped people to 112.",
        "after": "Use the road only after authorities clear it; follow marked diversions.",
    },
    "power outage": {
        "before": "Keep a torch and charged phone ready; unplug sensitive equipment if it is safe to do so.",
        "during": "Stay away from downed wires and flooded electrical equipment. Do not use lifts; call 112 for immediate danger.",
        "after": "Treat fallen wires as live and wait for the electricity department's all-clear before restoring circuits.",
    },
    "maritime emergency": {
        "before": "Report the vessel name, last known position, people aboard, and immediate hazards to 112 or the Coast Guard.",
        "during": "Wear a life jacket, stay with the vessel or flotation if safe, and follow Coast Guard instructions. Do not enter the water to attempt an untrained rescue.",
        "after": "Keep clear of rescue operations and report missing people or hazards to responders; do not circulate unverified coordinates.",
    },
    "abuse": {
        "before": "If safe, move to a place the person causing harm cannot access. Use a trusted device if your phone may be monitored.",
        "during": "If anyone is in immediate danger, call 112. Women can call 181 where available; for a child at risk, call 1098. Do not confront the person if that could increase danger.",
        "after": "Choose a trusted person or service to contact. You do not need to share names or detailed personal information in this app.",
    },
    "kidnapping": {
        "before": "For an active abduction or immediate threat, call 112 now and provide the last known location and time.",
        "during": "Do not confront or pursue a suspected abductor. Preserve messages or vehicle details only if safe; call 1098 if a child is involved.",
        "after": "Share updates directly with police/112. Avoid posting identifying details or unverified allegations publicly.",
    },
    "sexual assault": {
        "before": "If danger is ongoing, move to a safer place if possible and call 112. A trusted person can accompany the survivor.",
        "during": "Seek emergency medical care when needed. Do not delay care; the survivor may choose what to share. Women can also try 181 where available.",
        "after": "Avoid sharing names, images, or details publicly. Use a trusted device and ask responders about confidential support options.",
    },
    "child labour": {
        "before": "If a child is in immediate danger, call 112. Contact Child Helpline 1098 for child protection support.",
        "during": "Do not confront an employer or put the child at further risk. Share the location and immediate safety concern with 1098 or police.",
        "after": "Use the official Child Helpline or local child-protection authorities; avoid posting the child's identity online.",
    },
    "water problem": {
        "before": "For suspected contamination, stop using that source for drinking or cooking and use a known safe supply while seeking local advice.",
        "during": "Report supply failure, leaks, or contamination to the local water utility/municipality. Call 112 only for an immediate threat to life.",
        "after": "Follow public-health or municipal water-quality instructions before using the source again.",
    },
    "other": {
        "before": "Keep essential medicines, water, a torch, and emergency contacts accessible; follow local authority guidance.",
        "during": "Move away from immediate danger and call 112 if anyone needs urgent help.",
        "after": "Follow official instructions and report damage or unmet needs through verified channels.",
    },
}


def nearest_help(points, location, limit=5):
    if not location:
        return []
    origin = location.get("coordinates")
    if not origin:
        return []
    ranked = []
    for point in points:
        if not point.get("coordinates"):
            continue
        distance = haversine_km(origin, point["coordinates"])
        ranked.append({**point, "distance_km": round(distance, 1)})
    return sorted(ranked, key=lambda item: item["distance_km"])[:limit]


# -----------------------------------------------------------------------------
# 8. EMERGENCY CHATBOT & HAZARD/SENSITIVE DETECTORS
# -----------------------------------------------------------------------------
class MultimodalUnavailable(RuntimeError):
    pass


CHAT_HAZARD_TERMS = {
    "maritime emergency": ("ship", "vessel", "man overboard", "boat capsized", "at sea"),
    "earthquake": ("earthquake", "tremor", "shaking building"),
    "cyclone": ("cyclone", "storm surge"),
    "flood": ("flood", "water entering", "inundation", "submerged"),
    "heavy rain": ("heavy rain", "downpour", "cloudburst", "waterlogging"),
    "heatwave": ("heatwave", "heat wave", "heat stroke"),
    "tsunami": ("tsunami",),
    "wildfire": ("wildfire", "forest fire"),
    "landslide": ("landslide", "landslip"),
    "road blockage": ("road blocked", "road blockage", "tree fell", "landslide"),
    "accident": ("accident", "collision", "crash", "injured on road"),
    "medical emergency": ("unconscious", "bleeding", "heart attack", "not breathing", "injured"),
    "power outage": ("power outage", "power cut", "downed wire", "transformer"),
}
SENSITIVE_TERMS = {
    "sexual_assault": ("rape", "sexual assault", "molest", "sexually assaulted"),
    "kidnapping": ("kidnap", "abduct", "missing child", "taken by force"),
    "child_labour": ("child labour", "child labor", "forced child work"),
    "abuse": ("domestic violence", "abuse at home", "being beaten", "physical abuse"),
}


def detect_hazard(text):
    content = (text or "").casefold()
    for hazard, terms in CHAT_HAZARD_TERMS.items():
        if any(term in content for term in terms):
            return hazard
    return "other"


def offline_reply(text):
    hazard = detect_hazard(text)
    steps = GUIDANCE.get(hazard, GUIDANCE["other"])
    return (f"Possible hazard: {hazard.title()}. I can provide general guidance, but this report has not been verified.\n\n"
            f"Now: {steps['during']}\n\n"
            f"If anyone is in immediate danger, call {HELPLINE['number']}. Share the exact location with responders.")


def detect_sensitive(text):
    content = (text or "").casefold()
    return next((kind for kind, terms in SENSITIVE_TERMS.items()
                 if any(term in content for term in terms)), None)


def private_safety_reply(kind, voice_transcribed=False):
    contacts = {"sexual_assault": "112 or Women Helpline 181; for a child, 1098",
                "kidnapping": "112; for a child, Child Helpline 1098",
                "child_labour": "Child Helpline 1098 or 112",
                "abuse": "112; Women Helpline 181 where available; for a child, 1098"}
    if voice_transcribed:
        privacy_note = "The voice recording was sent to Groq for transcription before sensitive content was recognized; its transcript was not saved in chat history."
    else:
        privacy_note = "This typed sensitive disclosure was handled locally and omitted from chat history."
    return (f"{privacy_note} Do not use voice uploads for sensitive cases. "
            f"If anyone is in immediate danger, call {contacts[kind]} directly. This app does not contact them for you.")


class EmergencyChatbot:
    def __init__(self, llm):
        self.llm = llm

    def transcribe(self, audio_bytes, filename):
        if not self.llm.available:
            raise MultimodalUnavailable("Voice transcription requires GROQ_API_KEY.")
        response = self.llm.client.audio.transcriptions.create(
            file=(filename, audio_bytes), model=GROQ_AUDIO_MODEL, response_format="json")
        return response.text.strip()

    def reply(self, text, image_bytes=None, image_mime="image/jpeg", history=None, voice_transcribed=False):
        sensitive_type = detect_sensitive(text)
        if sensitive_type:
            return private_safety_reply(sensitive_type, voice_transcribed)
        if image_bytes and not self.llm.available:
            raise MultimodalUnavailable("Photo analysis requires GROQ_API_KEY. You can still ask about the image in text.")
        if not self.llm.available:
            return offline_reply(text)

        system = (
            "You are a cautious emergency safety assistant for people in India. Analyze the user's text and, if provided, "
            "the attached image. Clearly distinguish visible observations from uncertain guesses. Never claim an image "
            "proves an event or exact location. Do not diagnose injuries or tell people to take dangerous actions. Give "
            "brief practical safety guidance, ask for missing location/details when useful, and tell people to call 112 "
            "when there may be immediate danger. You are not an official warning authority; direct users to official responders."
        )
        content = [{"type": "text", "text": text or "Describe the visible emergency and give safe next steps."}]
        model = GROQ_MODEL
        if image_bytes:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{encoded}"}})
            model = GROQ_VISION_MODEL
        messages = [{"role": "system", "content": system}]
        messages.extend((history or [])[-8:])
        messages.append({"role": "user", "content": content})
        response = self.llm.client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=messages,
        )
        return response.choices[0].message.content.strip()


# -----------------------------------------------------------------------------
# 9. COORDINATOR / ORCHESTRATOR
# -----------------------------------------------------------------------------
class Coordinator:
    def __init__(self):
        self.llm, self.memory = LLM(), Memory()
        a = (self.llm, self.memory)
        self.incident, self.verify = IncidentAgent(*a), VerificationAgent(*a)
        self.resource, self.volunteer, self.notify = ResourceAgent(*a), VolunteerAgent(*a), NotificationAgent(*a)

    def status(self):
        return {"llm": "groq" if self.llm.available else "rules-fallback", "memory": self.memory.backend}

    def handle_report(self, report: str, location_context: dict | None = None,
                      official_alerts: list[dict] | None = None) -> dict:
        state = load()
        trace = []
        inc = self.incident.run(report, state, location_context)
        trace.append(("Incident", f"{inc['type']} sev{inc['severity']} @ {inc['location']}"))
        inc["id"] = f"INC-{len(state['incidents']) + 1:04d}"
        inc["created_at"] = dt.datetime.utcnow().isoformat()
        ver = self.verify.run(inc, state, official_alerts)
        trace.append(("Verification", f"verified={ver['verified']} conf={ver['confidence']}"))

        if ver["duplicate_of"]:
            original = next((item for item in state["incidents"] if item["id"] == ver["duplicate_of"]), None)
            if original:
                original["report_count"] = original.get("report_count", 1) + 1
                original.setdefault("timeline", []).append({"at": inc["created_at"], "event": "Corroborating report received"})
                save(state)
            self.memory.retain(f"Duplicate report merged into {ver['duplicate_of']}: {report}")
            return {"incident": inc, "verification": ver, "duplicate": True, "trace": trace}
        if not ver["verified"]:
            inc["status"] = "unverified"
            state["incidents"].append(self._record(inc, ver, {"allocated": [], "shortages": []}, {"assigned": []}))
            save(state)
            return {"incident": inc, "verification": ver, "trace": trace, "held": True,
                    "sensitive": inc["type"] in SENSITIVE_INCIDENT_TYPES}

        res = self.resource.run(inc, ver, state)
        trace.append(("Resource", f"{len(res['allocated'])} allocated, shortages={res['shortages']}"))
        vol = self.volunteer.run(inc, state)
        trace.append(("Volunteer", f"{len(vol['assigned'])} assigned"))
        inc["status"] = "dispatched"
        msgs = self.notify.run(inc, ver, res, vol)
        trace.append(("Notification", f"{len(msgs)} messages sent"))
        state["incidents"].append(self._record(inc, ver, res, vol))
        save(state)

        self.memory.retain(
            f"Incident {inc['id']}: {inc['type']} severity {inc['severity']} at {inc['location']}. {inc['summary']} "
            f"Dispatched: {[a['kind'] for a in res['allocated']]}. Volunteers: {[v['name'] for v in vol['assigned']]}. "
            f"Shortages: {res['shortages']}.", context="incident-dispatch")
        return {"incident": inc, "verification": ver, "resources": res, "volunteers": vol, "messages": msgs, "trace": trace}

    @staticmethod
    def _record(inc, ver, res, vol):
        return {"id": inc["id"], "type": inc["type"], "severity": inc["severity"], "location": inc["location"],
                "coords": inc["coords"], "summary": inc["summary"], "status": inc.get("status", "open"),
                "created_at": inc["created_at"], "confidence": ver["confidence"],
                "people_affected": inc.get("people_affected", 0), "report_count": 1,
                "state": inc.get("state", ""), "district": inc.get("district", ""),
                "source": "Citizen report", "official_alert_ids": ver.get("official_alert_ids", []),
                "preposition_recommendation": ver.get("preposition_recommendation", ""),
                "needs_human_check": ver.get("needs_human_check", False),
                "verification_reasons": ver.get("reasons", []),
                "resource_shortages": res.get("shortages", []),
                "resources": res["allocated"], "volunteers": [v["volunteer_id"] for v in vol["assigned"]],
                "volunteer_assignments": vol["assigned"],
                "timeline": [{"at": inc["created_at"], "event": f"Report received; verification confidence {ver['confidence']:.0%}"}]}

    def approve(self, incident_id: str) -> dict:
        state = load()
        inc = next((item for item in state["incidents"] if item["id"] == incident_id), None)
        if not inc or inc["status"] != "unverified":
            return {"error": "incident is not awaiting approval"}
        if inc["type"] in SENSITIVE_INCIDENT_TYPES:
            return {"error": "Sensitive incident: use the listed official helplines; this app cannot confidentially dispatch responders."}
        verification = {"confidence": inc["confidence"], "needs_human_check": False, "reasons": inc.get("verification_reasons", [])}
        resources = self.resource.run(inc, verification, state)
        volunteers = self.volunteer.run(inc, state)
        inc["resources"] = resources["allocated"]
        inc["volunteers"] = [item["volunteer_id"] for item in volunteers["assigned"]]
        inc["volunteer_assignments"] = volunteers["assigned"]
        inc["resource_shortages"] = resources["shortages"]
        inc["status"] = "dispatched"
        inc["approved_at"] = dt.datetime.utcnow().isoformat()
        inc.setdefault("timeline", []).append({"at": inc["approved_at"], "event": "Dispatcher approved; response dispatched"})
        messages = self.notify.run(inc, verification, resources, volunteers)
        save(state)
        self.memory.retain(f"Dispatcher approved incident {inc['id']} ({inc['type']} at {inc['location']}) and dispatched response.", context="incident-dispatch")
        return {"incident": inc, "resources": resources, "volunteers": volunteers, "messages": messages}

    def advance_status(self, incident_id: str, next_status: str) -> dict:
        transitions = {"dispatched": "en_route", "en_route": "on_scene"}
        state = load()
        inc = next((item for item in state["incidents"] if item["id"] == incident_id), None)
        if not inc or transitions.get(inc["status"]) != next_status:
            return {"error": "invalid response status transition"}
        now = dt.datetime.utcnow().isoformat()
        inc["status"] = next_status
        inc.setdefault("timeline", []).append({"at": now, "event": f"Response status updated to {next_status.replace('_', ' ')}"})
        save(state)
        return {"updated": incident_id, "status": next_status}

    def resolve(self, incident_id: str, notes: str = "", outcome: str = "success") -> dict:
        state = load()
        inc = next((i for i in state["incidents"] if i["id"] == incident_id), None)
        if not inc or inc["status"] == "closed":
            return {"error": "not found or already closed"}
        for a in inc["resources"]:
            for r in state["resources"]:
                if r["id"] == a["resource_id"]:
                    r["available"] = min(r["quantity"], r["available"] + a["qty"])
        for v in state["volunteers"]:
            if v["id"] in inc["volunteers"]:
                v["available"] = True
                if outcome == "success":
                    v["completed"] = v.get("completed", 0) + 1
        inc["status"], inc["resolved_at"] = "closed", dt.datetime.utcnow().isoformat()
        inc.setdefault("timeline", []).append({"at": inc["resolved_at"], "event": f"Incident resolved: {notes or 'no resolution notes'}"})
        save(state)
        self.memory.retain(
            f"RESOLVED {inc['id']} ({inc['type']} at {inc['location']}, sev {inc['severity']}) outcome={outcome}. "
            f"Resources used: {[a['kind'] for a in inc['resources']]}. Notes: {notes or 'none'}",
            context="resolution-history")
        return {"closed": inc["id"]}

    def ask(self, question: str) -> str:
        return self.memory.reflect(question)
