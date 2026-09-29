import re
import state as st
import config

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


class IncidentAgent:
    """Turns a free-text citizen report into a structured incident."""
    name = "IncidentAgent"

    def __init__(self, llm, memory):
        self.llm, self.memory = llm, memory

    def run(self, report: str, state: dict, location_context: dict | None = None) -> dict:
        context = location_context or {}
        selected_type = context.get("hazard_type")
        report_type = next((kind for kind, terms in KEYWORDS.items()
                            if kind in config.SENSITIVE_INCIDENT_TYPES
                            and any(term in report.casefold() for term in terms)), None)
        sensitive_type = selected_type if selected_type in config.SENSITIVE_INCIDENT_TYPES else report_type
        past = [] if sensitive_type else self.memory.recall(report, k=3)
        places = list(state["locations"].keys())
        if sensitive_type:
            d = self._rules(report, places)
            d["type"] = sensitive_type
        else:
            try:
                d = self.llm.json(
                    "You are an emergency intake analyst for India. Extract a structured incident. "
                    f"type must be one of {config.INCIDENT_TYPES}. severity is an int 1 (minor) to 5 (life-threatening). "
                    f"location must be the closest match from this list, or null: {places}. "
                    "Fields: type, severity, location, people_affected (int estimate), summary (<=25 words), "
                    "urgent_needs (list of short strings). Use past incidents only as context.",
                    f"Report: {report}\nSimilar past incidents: {past}")
            except Exception:
                d = self._rules(report, places)
        d["type"] = d.get("type") if d.get("type") in config.INCIDENT_TYPES else "other"
        d["severity"] = max(1, min(5, int(d.get("severity", 3))))
        if context.get("hazard_type") in config.INCIDENT_TYPES:
            d["type"] = context["hazard_type"]
        if d["type"] in config.SENSITIVE_INCIDENT_TYPES:
            d["summary"] = "Sensitive incident report; details withheld for confidential review."
        if context:
            d["state"] = context.get("state", "")
            d["district"] = context.get("district", "")
            d["location"] = context.get("location") or ", ".join(filter(None, [d["district"], d["state"]]))
            d["coords"] = context.get("coordinates") or st.coords(state, d["location"])
        else:
            if d.get("location") not in state["locations"]:
                d["location"] = next((p for p in places if p in report.lower()), "secunderabad")
            d["state"] = "Telangana"
            d["district"] = d["location"].title()
            d["coords"] = st.coords(state, d["location"])
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
