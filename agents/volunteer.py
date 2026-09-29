import state as st

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
            dist = st.haversine_km(inc["coords"], st.coords(state, v["location"]))
            scored.append((match * 10 - dist + min(v.get("completed", 0), 10) * 0.5, dist, v))
        scored.sort(key=lambda x: -x[0])
        chosen = []
        for _, dist, v in scored[:want_n]:
            v["available"] = False
            chosen.append({"volunteer_id": v["id"], "name": v["name"], "phone": v["phone"],
                           "matched_skills": sorted(wanted & set(v["skills"])), "distance_km": round(dist, 1),
                           "languages": v["languages"]})
        return {"assigned": chosen, "unfilled": max(0, want_n - len(chosen))}
