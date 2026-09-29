import datetime as dt
import state as st
import config


class VerificationAgent:
    """Cross-checks a new incident against the live registry + long-term memory:
    duplicates, corroboration by nearby reports, and historical patterns."""
    name = "VerificationAgent"

    def __init__(self, llm, memory):
        self.llm, self.memory = llm, memory

    def run(self, inc: dict, state: dict, official_alerts: list[dict] | None = None) -> dict:
        if inc["type"] in config.SENSITIVE_INCIDENT_TYPES:
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
            dist = st.haversine_km(inc["coords"], old["coords"])
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
        # Life-threatening reports are never blocked: dispatch + flag for a human.
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
