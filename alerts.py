"""Fetch and normalize official India Meteorological Department CAP alerts."""
import concurrent.futures
import datetime as dt
import email.utils
import xml.etree.ElementTree as ET

import requests


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
HAZARD_TERMS = {
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
    for hazard, terms in HAZARD_TERMS.items():
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
        coords = []
        for pair in polygon.split():
            try:
                latitude, longitude = map(float, pair.split(",", 1))
                coords.append((latitude, longitude))
            except (ValueError, TypeError):
                continue
        if coords:
            return [sum(point[0] for point in coords) / len(coords), sum(point[1] for point in coords) / len(coords)]
    return None


def parse_cap_alert(xml_text, now=None):
    """Normalize one CAP 1.2 document; return None when inactive or non-public."""
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
    """Fetch active IMD CAP items over verified HTTPS; never return feed rows as official on failure."""
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
    """Return a transparent 0-100 heuristic by state/area, keeping source counts separate."""
    areas = {}

    def get_area(state, district):
        key = (state or "Unknown", district or "Area not identified")
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
        from state import haversine_km
        if haversine_km(alert["coordinates"], place["coordinates"]) <= radius_km:
            return True
    return bool(alert.get("state") and place.get("state") and alert["state"].casefold() == place["state"].casefold()
                and area.strip() in (alert["state"].casefold(), f"all {alert['state'].casefold()}"))