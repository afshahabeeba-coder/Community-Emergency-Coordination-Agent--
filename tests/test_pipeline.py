import sys, pathlib, datetime as dt
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import state as st
import config
from alerts import area_risk_scores, demo_alerts, matches_saved_location, parse_cap_alert
from chatbot import EmergencyChatbot, MultimodalUnavailable, detect_hazard, detect_sensitive, offline_reply, private_safety_reply
from alerts import INDIAN_STATES
from agents.resource import DEFAULT_NEEDS
from agents.volunteer import SKILLS_BY_TYPE
from guidance import GUIDANCE, PRIMARY_CONTACT_BY_TYPE, nearest_help
from orchestrator import Coordinator


def test_full_cycle():
    st.reset()
    c = Coordinator()
    out = c.handle_report("Severe flooding in Kukatpally, 12 families trapped, elderly person unwell")
    assert out["incident"]["type"] == "flood" and out["incident"]["severity"] >= 4
    assert out["resources"]["allocated"] and out["volunteers"]["assigned"] and out["messages"]
    before = sum(r["available"] for r in st.load()["resources"])
    dup = c.handle_report("Severe flooding in Kukatpally, 12 families trapped, elderly person unwell")
    assert dup.get("duplicate")
    incident = next(i for i in st.load()["incidents"] if i["id"] == out["incident"]["id"])
    assert incident["report_count"] == 2
    assert c.advance_status(incident["id"], "en_route")["status"] == "en_route"
    assert c.advance_status(incident["id"], "on_scene")["status"] == "on_scene"
    assert c.advance_status(incident["id"], "en_route").get("error")
    assert c.resolve(out["incident"]["id"], "boats and pumps worked")["closed"]
    resolved = next(i for i in st.load()["incidents"] if i["id"] == out["incident"]["id"])
    assert any("resolved" in event["event"] for event in resolved["timeline"])
    assert sum(r["available"] for r in st.load()["resources"]) > before
    assert "flood" in c.ask("flood Kukatpally").lower()


def test_dispatcher_approval():
    st.reset()
    state = st.load()
    created_at = dt.datetime.utcnow().isoformat()
    state["incidents"].append({
        "id": "INC-REVIEW", "type": "medical_emergency", "severity": 2,
        "location": "ameerpet", "coords": st.coords(state, "ameerpet"),
        "summary": "A resident needs medical assistance", "status": "unverified",
        "created_at": created_at, "confidence": 0.42, "people_affected": 1,
        "verification_reasons": ["Awaiting dispatcher review"], "resources": [],
        "volunteers": [], "timeline": [{"at": created_at, "event": "Report held for review"}],
    })
    st.save(state)

    result = Coordinator().approve("INC-REVIEW")
    assert result["incident"]["status"] == "dispatched"
    assert result["resources"]["allocated"]
    assert result["volunteers"]["assigned"]
    assert "Dispatcher approved" in result["incident"]["timeline"][-1]["event"]


def test_cap_alert_parsing_and_expiry():
        now = dt.datetime(2026, 9, 29, tzinfo=dt.timezone.utc)
        cap = """<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
            <identifier>CAP-ASSAM-1</identifier><sender>imd@example.gov.in</sender>
            <status>Actual</status><scope>Public</scope><sent>2026-09-29T10:00:00Z</sent>
            <info><event>Flood</event><severity>Severe</severity><headline>Flood warning</headline>
                <description>Flood danger near the river.</description><expires>2026-09-30T10:00:00Z</expires>
                <area><areaDesc>Kamrup, Assam</areaDesc>
                    <geocode><valueName>District</valueName><value>Kamrup</value></geocode>
                    <polygon>26.1,91.7 26.2,91.8 26.3,91.7</polygon>
                </area></info>
        </alert>"""
        alert = parse_cap_alert(cap, now=now)
        assert alert["source"] == "Official" and alert["is_live"]
        assert alert["state"] == "Assam" and alert["district"] == "Kamrup"
        assert alert["hazard"] == "flood" and alert["color"] == "Orange"
        assert alert["coordinates"] and alert["expires"]
        assert parse_cap_alert(cap.replace("2026-09-30T10:00:00Z", "2026-09-28T10:00:00Z"), now=now) is None
        assert parse_cap_alert(cap.replace("<status>Actual</status>", "<status>Exercise</status>"), now=now) is None
        assert demo_alerts(now)[0]["source"] == "DEMO SIMULATED"


def test_official_alert_cross_verification():
    st.reset()
    official = {"id": "CAP-TG-1", "source": "Official", "is_live": True, "status": "Active",
                "hazard": "heavy rain", "color": "Orange", "state": "Telangana",
                "district": "Hyderabad", "area": "Hyderabad, Telangana"}
    location = {"state": "Telangana", "district": "Hyderabad", "location": "Kukatpally, Hyderabad, Telangana",
                "coordinates": [17.4948, 78.3996]}
    out = Coordinator().handle_report("Heavy rain flooding near Kukatpally, 8 people affected", location, [official])
    assert out["verification"]["official_alert_ids"] == ["CAP-TG-1"]
    assert out["verification"]["confidence"] >= 0.65
    assert out["verification"]["preposition_recommendation"]
    assert out["incident"]["severity"] >= 4
    saved_incident = next(item for item in st.load()["incidents"] if item["id"] == out["incident"]["id"])
    assert saved_incident["coords"] == location["coordinates"]
    assert saved_incident["state"] == location["state"]
    assert saved_incident["district"] == location["district"]
    assert location["location"] in saved_incident["location"]

    st.reset()
    unrelated = {**official, "id": "CAP-TN-1", "state": "Tamil Nadu", "district": "Chennai",
                 "area": "Chennai, Tamil Nadu"}
    out = Coordinator().handle_report("Heavy rain flooding near Kukatpally, 8 people affected", location, [unrelated])
    assert out["verification"]["official_alert_ids"] == []
    st.reset()


def test_area_risk_keeps_sources_visible():
    alert = {"id": "CAP-TG-2", "source": "Official", "is_live": True, "color": "Orange",
             "state": "Telangana", "district": "Hyderabad"}
    incident = {"state": "Telangana", "district": "Hyderabad", "status": "dispatched",
                "severity": 5, "report_count": 1}
    rows = area_risk_scores([alert], [], [incident])
    assert len(rows) == 1
    assert rows[0]["official_alerts"] == 1 and rows[0]["citizen_reports"] == 1
    assert rows[0]["risk"] == "High" and rows[0]["risk_score"] <= 100


def test_saved_location_alert_matching():
    place = {"state": "Telangana", "district": "Hyderabad", "coordinates": [17.385, 78.4867]}
    same_district = {"state": "Telangana", "district": "Hyderabad", "area": "Hyderabad, Telangana"}
    far_same_state = {"state": "Telangana", "district": "Warangal", "area": "Warangal, Telangana",
                      "coordinates": [17.9689, 79.5941]}
    nearby = {"state": "Telangana", "district": "Unknown", "area": "", "coordinates": [17.39, 78.49]}
    other_state = {"state": "Tamil Nadu", "district": "Chennai", "area": "Chennai, Tamil Nadu",
                   "coordinates": [13.0827, 80.2707]}
    assert matches_saved_location(same_district, place)
    assert not matches_saved_location(far_same_state, place)
    assert matches_saved_location(nearby, place)
    assert not matches_saved_location(other_state, place)


def test_nearest_help_points_are_distance_sorted():
    points = [
        {"name": "Far shelter", "kind": "Shelter", "coordinates": [18.0, 79.0]},
        {"name": "Nearby hospital", "kind": "Hospital", "coordinates": [17.39, 78.49]},
    ]
    result = nearest_help(points, {"coordinates": [17.385, 78.4867]})
    assert result[0]["name"] == "Nearby hospital"
    assert result[0]["distance_km"] < result[1]["distance_km"]


def test_maritime_emergency_dispatches_boat():
    st.reset()
    location = {"state": "Maharashtra", "district": "Mumbai City", "location": "Mumbai, Maharashtra",
                "coordinates": [19.076, 72.8777], "hazard_type": "maritime_emergency"}
    result = Coordinator().handle_report("Ship sinking, crew in distress and one person overboard", location)
    assert result["incident"]["type"] == "maritime_emergency"
    assert result["incident"]["severity"] >= 4
    assert any(item["kind"] == "boat" for item in result["resources"]["allocated"])
    assert any(item["kind"] == "ambulance" for item in result["resources"]["allocated"])
    assert any(item["kind"] == "first_aid" for item in result["resources"]["allocated"])
    st.reset()


def test_national_hazard_catalog_is_complete():
    assert len(INDIAN_STATES) == 36
    assert "maritime_emergency" in config.INCIDENT_TYPES
    assert set(config.INCIDENT_TYPES) == set(DEFAULT_NEEDS) == set(SKILLS_BY_TYPE)
    assert all(kind in GUIDANCE for kind in ("flood", "earthquake", "accident", "abuse", "kidnapping",
                                              "sexual assault", "child labour", "water problem"))
    assert PRIMARY_CONTACT_BY_TYPE["abuse"] == "181"
    assert PRIMARY_CONTACT_BY_TYPE["sexual_assault"] == "181"
    assert PRIMARY_CONTACT_BY_TYPE["child_labour"] == "1098"
    assert PRIMARY_CONTACT_BY_TYPE["accident"] == "108"
    assert PRIMARY_CONTACT_BY_TYPE["flood"] == "1078"
    assert PRIMARY_CONTACT_BY_TYPE["earthquake"] == "1078"


def test_emergency_chat_offline_and_multimodal_safety():
    class OfflineLLM:
        available = False

    chatbot = EmergencyChatbot(OfflineLLM())
    assert detect_hazard("A ship is sinking with people overboard") == "maritime emergency"
    assert "112" in offline_reply("Flood water is entering a home")
    assert detect_sensitive("I was raped") == "sexual_assault"
    assert "Women Helpline 181" in private_safety_reply("sexual_assault")
    assert "sent to Groq" in private_safety_reply("sexual_assault", voice_transcribed=True)
    try:
        chatbot.transcribe(b"audio", "report.wav")
    except MultimodalUnavailable:
        pass
    else:
        raise AssertionError("offline transcription must not pretend to work")
    try:
        chatbot.reply("", b"image", "image/jpeg")
    except MultimodalUnavailable:
        pass
    else:
        raise AssertionError("offline image analysis must not pretend to work")
    class AvailableLLM:
        available = True

        @property
        def client(self):
            raise AssertionError("sensitive chatbot text must bypass the AI client")

    private_reply = EmergencyChatbot(AvailableLLM()).reply("I was raped")
    assert "omitted from chat history" in private_reply


def test_sensitive_incident_is_redacted_and_not_broadcast():
    st.reset()
    location = {"state": "Telangana", "district": "Hyderabad", "location": "Hyderabad, Telangana",
                "coordinates": [17.385, 78.4867], "hazard_type": "sexual_assault"}
    outbox_before = config.OUTBOX_FILE.read_bytes() if config.OUTBOX_FILE.exists() else b""
    result = Coordinator().handle_report("Private survivor name should never persist", location)
    stored = next(item for item in st.load()["incidents"] if item["id"] == result["incident"]["id"])
    outbox_after = config.OUTBOX_FILE.read_bytes() if config.OUTBOX_FILE.exists() else b""
    assert result["held"] and result["sensitive"]
    assert stored["status"] == "unverified"
    assert "Private survivor name" not in stored["summary"]
    assert "raw_report" not in stored
    assert outbox_before == outbox_after
    assert "Sensitive incident" in Coordinator().approve(result["incident"]["id"])["error"]

    st.reset()
    location.pop("hazard_type")
    result = Coordinator().handle_report("A child labour concern; private details should not persist", location)
    assert result["sensitive"] and result["incident"]["type"] == "child_labour"
    stored = next(item for item in st.load()["incidents"] if item["id"] == result["incident"]["id"])
    assert "private details" not in stored["summary"].lower()
    st.reset()


if __name__ == "__main__":
    test_full_cycle()
    test_dispatcher_approval()
    test_cap_alert_parsing_and_expiry()
    test_official_alert_cross_verification()
    test_area_risk_keeps_sources_visible()
    test_saved_location_alert_matching()
    test_nearest_help_points_are_distance_sorted()
    test_maritime_emergency_dispatches_boat()
    test_national_hazard_catalog_is_complete()
    test_emergency_chat_offline_and_multimodal_safety()
    test_sensitive_incident_is_redacted_and_not_broadcast()
    print("OK")
