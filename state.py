"""Structured operational state (live inventory, volunteers, incident registry).
Hindsight holds the *semantic/long-term* memory; this holds the *transactional* numbers
(e.g. exactly 2 boats left) that must never be fuzzy."""
import copy, json, math, shutil
import config


def load() -> dict:
    if not config.STATE_FILE.exists():
        shutil.copy(config.SEED_FILE, config.STATE_FILE)
    data = json.loads(config.STATE_FILE.read_text())
    seed = json.loads(config.SEED_FILE.read_text())
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
    config.STATE_FILE.write_text(json.dumps(state, indent=2))


def reset() -> None:
    shutil.copy(config.SEED_FILE, config.STATE_FILE)


def coords(state: dict, place: str):
    return tuple(state["locations"].get((place or "").lower().strip(), (17.385, 78.4867)))


def haversine_km(a, b) -> float:
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(d))
