import state as st

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


class ResourceAgent:
    """Chooses resource kinds (LLM, informed by memory) and allocates the nearest available units."""
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
                           key=lambda r: st.haversine_km(inc["coords"], st.coords(state, r["location"])))
            if not cands:
                shortages.append(kind)
                continue
            r = cands[0]
            take = min(qty, r["available"])
            r["available"] -= take
            allocated.append({"resource_id": r["id"], "kind": kind, "name": r["name"], "qty": take, "from": r["location"],
                              "distance_km": round(st.haversine_km(inc["coords"], st.coords(state, r["location"])), 1)})
        return {"allocated": allocated, "shortages": shortages}
