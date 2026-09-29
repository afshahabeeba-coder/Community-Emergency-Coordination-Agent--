"""Curated emergency guidance and demo help points."""
import state


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
        distance = state.haversine_km(origin, point["coordinates"])
        ranked.append({**point, "distance_km": round(distance, 1)})
    return sorted(ranked, key=lambda item: item["distance_km"])[:limit]