import datetime as dt
from urllib.parse import quote
import pandas as pd
import pydeck as pdk
import streamlit as st

import config
import state as store
from streamlit_geolocation import streamlit_geolocation
from alerts import INDIAN_STATES, AlertFeedError, area_risk_scores, demo_alerts, fetch_official_alerts, matches_saved_location
from chatbot import EmergencyChatbot, MultimodalUnavailable, detect_sensitive
from guidance import (GUIDANCE, HELPLINE, NEARBY_HELP_SEARCHES, PRIMARY_CONTACT_BY_TYPE, SUPPORT_CONTACTS,
                      SUPPORT_CONTACTS_BY_TYPE, nearest_help)
from orchestrator import Coordinator

HAZARD_LABELS = {
    "flood": "Flood", "cyclone": "Cyclone", "heavy_rain": "Heavy rain", "heatwave": "Heatwave",
    "earthquake": "Earthquake", "tsunami": "Tsunami", "wildfire": "Wildfire", "landslide": "Landslide",
    "maritime_emergency": "Ship / maritime emergency", "medical_emergency": "Medical emergency",
    "abuse": "Abuse / domestic violence", "kidnapping": "Kidnapping / missing person",
    "sexual_assault": "Sexual assault", "child_labour": "Child labour",
    "water_problem": "Water supply / contamination problem",
    "road_blockage": "Road blockage", "power_outage": "Power outage", "accident": "Road accident",
    "other": "Other",
}
HAZARD_TYPES_BY_LABEL = {label: hazard for hazard, label in HAZARD_LABELS.items()}


st.set_page_config(page_title="Community Emergency Coordination", page_icon="🚨", layout="wide")
if st.session_state.pop("clear_emergency_chat_inputs", False):
    for input_key in ("emergency_chat_text", "emergency_chat_audio", "emergency_chat_image"):
        st.session_state.pop(input_key, None)
if st.session_state.pop("clear_sensitive_report_text", False):
    st.session_state["incident_report_text"] = ""


@st.cache_resource
def coord():
    return Coordinator()


@st.cache_data(ttl=300, show_spinner=False)
def load_official_alerts(feed_url):
    return fetch_official_alerts(feed_url)


def _map_rows(alerts, incidents):
    rows = []
    for alert in alerts:
        coords = alert.get("coordinates")
        if coords and len(coords) == 2:
            rows.append({"latitude": coords[0], "longitude": coords[1], "label": alert["title"],
                         "source": alert["source"], "color": [220, 38, 38, 210] if alert["color"] == "Red"
                         else [234, 88, 12, 210] if alert["color"] == "Orange"
                         else [202, 138, 4, 210] if alert["color"] == "Yellow" else [22, 163, 74, 210],
                         "radius": 14000})
    for incident in incidents:
        coords = incident.get("coords")
        if coords and len(coords) == 2:
            rows.append({"latitude": coords[0], "longitude": coords[1], "label": incident["summary"],
                         "source": "Citizen report", "color": [37, 99, 235, 220], "radius": 8000})
    return rows


def _show_emergency_contacts(hazard, show_all=False):
    if show_all:
        st.caption("General emergency and specialist contacts")
        columns = st.columns(len(SUPPORT_CONTACTS))
        for column, contact in zip(columns, SUPPORT_CONTACTS.values()):
            column.link_button(f"Call {contact['number']} · {contact['label']}", f"tel:{contact['number']}")
            column.caption(contact["url"])
        return
    if hazard in ("water_problem", "power_outage"):
        service = "water-supply" if hazard == "water_problem" else "electricity"
        st.info(f"There is no single nationwide {service} utility number. Use the nearby local-service search; call 112 only if someone faces immediate danger.")
        return
    contact_ids = SUPPORT_CONTACTS_BY_TYPE.get(hazard, ["112"])
    primary_id = PRIMARY_CONTACT_BY_TYPE.get(hazard, contact_ids[0])
    primary = SUPPORT_CONTACTS[primary_id]
    st.caption(f"Primary emergency contact for {HAZARD_LABELS.get(hazard, 'this incident')}")
    st.link_button(f"Call {primary['number']} now · {primary['label']}",
                   f"tel:{primary['number']}", type="primary")
    st.caption("This opens your phone app; confirm the call there. The app does not place calls automatically.")
    secondary_ids = [contact_id for contact_id in contact_ids if contact_id != primary_id]
    if secondary_ids:
        columns = st.columns(len(secondary_ids))
        for column, contact_id in zip(columns, secondary_ids):
            contact = SUPPORT_CONTACTS[contact_id]
            column.link_button(f"Call {contact['number']} · {contact['label']}", f"tel:{contact['number']}")
            column.caption(contact["url"])


def _show_nearby_searches(hazard, coordinates):
    searches = NEARBY_HELP_SEARCHES.get(hazard, ["police station", "hospital"])
    st.caption("Map results are external and unverified. Opening a search shares its location with Google Maps.")
    columns = st.columns(min(len(searches), 3))
    for index, search in enumerate(searches):
        query = quote(f"{search} near {coordinates[0]},{coordinates[1]}")
        url = f"https://www.google.com/maps/search/?api=1&query={query}"
        columns[index % len(columns)].link_button(f"Find nearby {search}", url)


c = coord()
emergency_chat = EmergencyChatbot(c.llm)
s = store.load()
try:
    live_alerts = load_official_alerts(config.IMD_CAP_FEED_URL)
    now = dt.datetime.now(dt.timezone.utc)
    live_alerts = [item for item in live_alerts if not item.get("expires")
                   or dt.datetime.fromisoformat(item["expires"]) > now]
    feed_status = "LIVE · India Meteorological Department CAP feed"
    feed_error = ""
    store.record_official_alerts(live_alerts)
except AlertFeedError as error:
    live_alerts = []
    feed_status = "OFFICIAL FEED UNAVAILABLE"
    feed_error = str(error)

alerts = list(live_alerts)
if feed_error and config.RESQNET_DEMO_MODE:
    alerts = demo_alerts()

st.title("🚨 Community Emergency Coordination Agent")
st.caption(f"Alert data: **{feed_status}** · AI: **{c.status()['llm']}** · Memory: **{c.status()['memory']}**")
st.error(f"General emergency for immediate danger · {HELPLINE['number']} · {HELPLINE['label']}")
if feed_error:
    st.warning(f"{feed_error}. Any displayed demo alerts are simulated and are not official warnings.")
if alerts and not any(item.get("is_live") for item in alerts):
    st.info("DEMO SIMULATION · These sample alerts and help points are not live or official data.")

visible_incidents = [item for item in s["incidents"] if item["type"] not in config.SENSITIVE_INCIDENT_TYPES]
active_incidents = [item for item in visible_incidents if item.get("status") != "closed"]
tab_home, tab_chat, tab_alerts, tab_locations, tab_report, tab_incidents, tab_guidance, tab_assets, tab_memory = st.tabs([
    "Home", "Emergency chat", "National alerts", "My locations", "Incidents & help", "Incident map & history",
    "Safety & nearest help", "Response assets", "Ask memory",
])

with tab_home:
    st.subheader("Danger near your location")
    st.write("Share your device location, or monitor a saved place. Device coordinates stay in this session and are not added to incident reports automatically.")
    device_result = streamlit_geolocation()
    if isinstance(device_result, dict) and device_result.get("latitude") is not None and device_result.get("longitude") is not None:
        st.session_state["device_coordinates"] = [float(device_result["latitude"]), float(device_result["longitude"])]
        st.session_state["device_accuracy"] = device_result.get("accuracy")
    device_coordinates = st.session_state.get("device_coordinates")
    saved_locations = s.get("saved_locations", [])
    saved_labels = [f"{item['name']} · {item['district']}, {item['state']}" for item in saved_locations]
    watch_options = (["Current device location"] if device_coordinates else []) + saved_labels
    if watch_options:
        monitor = st.selectbox("Location being monitored", watch_options, key="home_location_monitor")
        if monitor == "Current device location":
            watch_location = {"name": "Current device location", "coordinates": device_coordinates, "state": "", "district": ""}
            accuracy = st.session_state.get("device_accuracy", "unknown")
            st.caption(f"Device location · {device_coordinates[0]:.5f}, {device_coordinates[1]:.5f} · accuracy {accuracy} m")
        else:
            watch_location = saved_locations[saved_labels.index(monitor)]
            st.caption(f"Saved location · {watch_location['district']}, {watch_location['state']}")

        from state import haversine_km
        nearby_alerts = [alert for alert in live_alerts
                         if alert.get("status") == "Active" and matches_saved_location(alert, watch_location)]
        nearby_incidents = []
        for incident in active_incidents:
            distance = haversine_km(watch_location["coordinates"], incident["coords"])
            if distance <= 50:
                nearby_incidents.append((distance, incident))

        overview = st.columns(3)
        overview[0].metric("Official warnings nearby", len(nearby_alerts))
        overview[1].metric("Citizen reports within 50 km", len(nearby_incidents))
        overview[2].metric("Location accuracy", f"{st.session_state.get('device_accuracy', '—')} m" if monitor == "Current device location" else "Saved point")
        for alert in nearby_alerts:
            message = f"{alert['color']} official warning · {alert['title']} · {alert.get('area', '')}"
            if alert.get("expires"):
                message += f" · expires {alert['expires']}"
            if alert["color"] in ("Red", "Orange"):
                st.error(message)
            else:
                st.warning(message)
        for distance, incident in sorted(nearby_incidents, key=lambda item: item[0]):
            confidence_note = "unverified citizen report" if incident.get("status") == "unverified" else "citizen report"
            st.warning(f"{confidence_note.title()} · {distance:.1f} km away · {incident['type'].replace('_', ' ')} · {incident['location']}")
        if not nearby_alerts and not nearby_incidents:
            st.success("No active official IMD warnings or open citizen incidents found near this location.")

        map_rows = _map_rows(nearby_alerts, [incident for _, incident in nearby_incidents])
        map_rows.append({"latitude": watch_location["coordinates"][0], "longitude": watch_location["coordinates"][1],
                         "label": watch_location["name"], "source": "Your location", "color": [14, 116, 144, 255], "radius": 1200})
        layer = pdk.Layer("ScatterplotLayer", data=map_rows, get_position="[longitude, latitude]",
                           get_fill_color="color", get_radius="radius", pickable=True,
                           radius_min_pixels=6, radius_max_pixels=20, opacity=0.85)
        st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=watch_location["coordinates"][0],
                                                                  longitude=watch_location["coordinates"][1], zoom=9.0),
                                 layers=[layer], tooltip={"text": "{source}\n{label}"}), use_container_width=True)
        st.caption("Blue-green marker: monitored location · Red/orange/yellow/green: official IMD warning · Blue: citizen report. CAP warning points may be approximate area centroids.")
    else:
        st.info("Use the location button above to find danger near your device, or add a home/work place in My locations.")

with tab_chat:
    st.subheader("Emergency safety assistant")
    st.write("Describe a danger, upload a voice recording, or attach a photo. The assistant provides guidance; it does not verify reports or dispatch responders.")
    if not c.llm.available:
        st.info("Text guidance works offline. Add GROQ_API_KEY to enable voice transcription and image analysis.")
    st.caption("Voice and photo uploads are sent to Groq when configured. Do not upload identifying voice details or intimate/identifiable images about abuse, kidnapping, or sexual violence. Use the sensitive incident report for local-only handling.")
    chat_history = st.session_state.setdefault("emergency_chat_history", [])
    for message in chat_history:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    chat_text = st.text_area("Describe the danger", placeholder="What happened? Include the location and whether anyone is in immediate danger.", key="emergency_chat_text")
    audio_upload = st.file_uploader("Voice recording", type=["mp3", "wav", "m4a", "webm"], key="emergency_chat_audio")
    if audio_upload and audio_upload.size > 20 * 1024 * 1024:
        st.error("Voice recording must be 20 MB or smaller.")
        audio_upload = None
    image_upload = st.file_uploader("Danger photo", type=["jpg", "jpeg", "png", "webp"], key="emergency_chat_image")
    if image_upload and image_upload.size > 10 * 1024 * 1024:
        st.error("Photo must be 10 MB or smaller.")
        image_upload = None
    if image_upload:
        st.image(image_upload, caption="Photo to analyze", use_container_width=True)
    if st.button("Ask safety assistant", type="primary", key="send_emergency_chat"):
        prompt = chat_text.strip()
        limitations = []
        transcript = ""
        if audio_upload:
            if detect_sensitive(prompt):
                limitations.append("Voice was not processed because the typed message appears sensitive.")
            else:
                try:
                    transcript = emergency_chat.transcribe(audio_upload.getvalue(), audio_upload.name)
                    prompt = f"{prompt}\nVoice report: {transcript}".strip()
                except MultimodalUnavailable as error:
                    limitations.append(str(error))
        if not prompt and not image_upload:
            st.error("Enter text or attach audio/photo before sending.")
        elif audio_upload and not prompt and limitations and not image_upload:
            st.error("Voice transcription needs GROQ_API_KEY. Add a text description or configure the key.")
        else:
            image_bytes = image_upload.getvalue() if image_upload else None
            mime = image_upload.type if image_upload else "image/jpeg"
            sensitive_type = detect_sensitive(prompt)
            user_message = ("[Sensitive safety request handled locally; personal text withheld from chat history]"
                            if sensitive_type else prompt or "Please assess the attached emergency photo.")
            if transcript:
                if sensitive_type:
                    user_message += "\n\n[Sensitive voice transcript withheld from chat history]"
                    limitations.append("The voice recording was sent to Groq for transcription before sensitive content was recognized.")
                else:
                    user_message += f"\n\nVoice transcript: {transcript}"
            elif audio_upload:
                user_message += "\n\n[Voice recording attached; transcription was unavailable]"
            if image_upload:
                user_message += "\n\n[Photo attached]"
            history = [{"role": item["role"], "content": item["content"]} for item in chat_history[-8:]]
            try:
                answer = emergency_chat.reply(prompt, image_bytes, mime, history, voice_transcribed=bool(transcript))
            except MultimodalUnavailable as error:
                limitations.append(str(error))
                if prompt:
                    answer = emergency_chat.reply(prompt, history=history, voice_transcribed=bool(transcript))
                else:
                    answer = "I can’t inspect the photo until image analysis is configured. If anyone may be in immediate danger, call 112 and describe the location."
            except Exception as error:
                limitations.append(f"Multimodal analysis failed: {error}")
                try:
                    answer = emergency_chat.reply(prompt, voice_transcribed=bool(transcript)) if prompt else "I couldn't analyze that upload. Call 112 if there is immediate danger."
                except Exception:
                    answer = "I couldn't analyze that request. If anyone may be in immediate danger, call 112 and share the exact location."
            if limitations:
                answer = "Capability note: " + " ".join(dict.fromkeys(limitations)) + "\n\n" + answer
            chat_history.extend([{"role": "user", "content": user_message}, {"role": "assistant", "content": answer}])
            st.session_state["emergency_chat_history"] = chat_history[-20:]
            st.session_state["clear_emergency_chat_inputs"] = True
            st.rerun()

with tab_alerts:
    metric_cols = st.columns(4)
    metric_cols[0].metric("Active official alerts", sum(1 for item in live_alerts if item["status"] == "Active"))
    metric_cols[1].metric("Citizen incidents", len(active_incidents))
    metric_cols[2].metric("People reported affected", sum(item.get("people_affected", 0) for item in active_incidents))
    metric_cols[3].metric("States covered by current feed", len({item["state"] for item in live_alerts if item.get("state")}))

    filters = st.columns([1, 1, 1, 1, 1])
    states = ["All states / UTs"] + INDIAN_STATES
    selected_state = filters[0].selectbox("State", states)
    hazard_labels = ["All hazards"] + list(HAZARD_TYPES_BY_LABEL)
    selected_hazard = filters[1].selectbox("Hazard", hazard_labels)
    selected_hazard_key = HAZARD_TYPES_BY_LABEL.get(selected_hazard, "")
    selected_severity = filters[2].selectbox("Severity", ["All levels", "Red", "Orange", "Yellow", "Green"])
    district_query = filters[3].text_input("District / area", placeholder="Search affected area")
    if filters[4].button("Refresh alerts", use_container_width=True):
        load_official_alerts.clear()
        st.rerun()

    filtered_alerts = [item for item in alerts
                       if (selected_state == "All states / UTs" or item.get("state") == selected_state)
                       and (selected_hazard == "All hazards" or item["hazard"].replace(" ", "_") == selected_hazard_key)
                       and (selected_severity == "All levels" or item["color"] == selected_severity)
                       and (not district_query or district_query.casefold() in f"{item.get('district', '')} {item.get('area', '')}".casefold())]

    st.subheader("Current alert feed")
    if not filtered_alerts:
        st.info("No active official warnings match these filters. This feed covers IMD meteorological CAP alerts, not every hazard or agency.")
    for alert in filtered_alerts:
        if alert["color"] == "Red":
            st.error(f"RED · {alert['title']}")
        elif alert["color"] == "Orange":
            st.warning(f"ORANGE · {alert['title']}")
        elif alert["color"] == "Yellow":
            st.info(f"YELLOW · {alert['title']}")
        else:
            st.success(f"GREEN · {alert['title']}")
        st.markdown(f"**Source:** {alert['source']} · **Authority:** {alert['authority']} · **Hazard:** {alert['hazard'].title()}")
        st.write(f"**Affected area:** {alert.get('area') or 'Area not specified'} · **State:** {alert.get('state') or 'Not identified'}")
        st.write(alert["summary"])
        st.caption(f"Issued: {alert.get('sent_at') or 'Not provided'} · Expires: {alert.get('expires') or 'Not provided'} · Status: {alert['status']}")
        if alert.get("instruction"):
            st.write(f"Authority guidance: {alert['instruction']}")
        if alert.get("url"):
            st.link_button("Open official bulletin", alert["url"])

with tab_locations:
    st.subheader("Saved warning locations")
    locations = s.get("saved_locations", [])
    if locations:
        place_labels = [f"{item['name']} · {item['district']}, {item['state']}" for item in locations]
        selected_place = st.selectbox("Watch location", place_labels)
        place = locations[place_labels.index(selected_place)]
        matching = [item for item in live_alerts if matches_saved_location(item, place)]
        if matching:
            st.warning(f"{len(matching)} active official warning(s) overlap or are near {place['name']}.")
            for alert in matching:
                st.write(f"{alert['color']} · {alert['title']} · {alert.get('area', '')} · expires {alert.get('expires') or 'not stated'}")
        else:
            st.success("No active official IMD alert matches this saved location.")
        from state import haversine_km
        nearby = []
        for incident in active_incidents:
            distance = haversine_km(place["coordinates"], incident["coords"])
            if distance <= 100:
                nearby.append((distance, incident))
        st.subheader("Nearby citizen reports")
        if nearby:
            for distance, incident in sorted(nearby, key=lambda pair: pair[0]):
                st.warning(f"Citizen report {distance:.1f} km away · {incident['type'].replace('_', ' ')} · {incident['location']} · confidence {incident['confidence']:.0%}")
        else:
            st.info("No active citizen incidents within 100 km of this saved point.")
    else:
        st.info("Save a home, work, school, or family location to see matching official alerts and nearby citizen reports.")

    with st.form("save_location_form"):
        st.markdown("**Add a location**")
        col_a, col_b, col_c = st.columns(3)
        location_name = col_a.text_input("Label", placeholder="Home, work, school")
        location_state = col_b.selectbox("State / UT", INDIAN_STATES, index=INDIAN_STATES.index("Telangana"))
        location_district = col_c.text_input("District", value="Hyderabad")
        col_d, col_e = st.columns(2)
        location_lat = col_d.number_input("Latitude", min_value=6.0, max_value=38.0, value=17.385, format="%.5f")
        location_lon = col_e.number_input("Longitude", min_value=68.0, max_value=98.0, value=78.4867, format="%.5f")
        if st.form_submit_button("Save location"):
            if not location_name.strip() or not location_district.strip():
                st.error("Enter a label and district.")
            else:
                s["saved_locations"].append({"name": location_name.strip(), "state": location_state,
                                              "district": location_district.strip(), "coordinates": [location_lat, location_lon]})
                store.save(s)
                st.rerun()

with tab_report:
    st.subheader("Get help and report an incident")
    st.write("Choose the incident type to get the relevant call link, safety guidance, nearby service search, and report category. Submitting a report does not contact emergency services.")
    help_hazard_label = st.selectbox("Incident type", ["Let agents classify"] + list(HAZARD_TYPES_BY_LABEL), key="incidents_help_type")
    help_hazard = HAZARD_TYPES_BY_LABEL.get(help_hazard_label)
    _show_emergency_contacts(help_hazard)
    help_guidance = GUIDANCE.get(help_hazard.replace("_", " ") if help_hazard else "other", GUIDANCE["other"])
    st.info(help_guidance["during"])
    if help_hazard in config.SENSITIVE_INCIDENT_TYPES:
        st.warning("For sensitive incidents, use the official phone services above. The app does not alert police, child protection, or a helpline.")
    help_locations = s.get("saved_locations", [])
    help_device_coordinates = st.session_state.get("device_coordinates")
    help_location_labels = (["Current device location"] if help_device_coordinates else []) + [
        f"{item['name']} · {item['district']}, {item['state']}" for item in help_locations]
    help_coordinates = None
    if help_location_labels:
        help_location_choice = st.selectbox("Find nearby help from", help_location_labels, key="incidents_help_location")
        if help_location_choice == "Current device location":
            help_coordinates = help_device_coordinates
        else:
            chosen_place = help_locations[help_location_labels.index(help_location_choice) - (1 if help_device_coordinates else 0)]
            help_coordinates = chosen_place["coordinates"]
    else:
        st.caption("Use the location button on Home or save a place in My locations to search nearby services.")
    if help_coordinates:
        _show_nearby_searches(help_hazard, help_coordinates)
        from state import haversine_km
        nearby_active = [(haversine_km(help_coordinates, item["coords"]), item) for item in active_incidents]
        nearby_active = [(distance, item) for distance, item in nearby_active if distance <= 50]
        st.markdown("**Open incidents within 50 km**")
        if nearby_active:
            st.dataframe(pd.DataFrame([{"ID": item["id"], "Type": HAZARD_LABELS.get(item["type"], item["type"]),
                                        "Location": item["location"], "Distance km": round(distance, 1),
                                        "Status": item["status"].replace("_", " ").title()}
                                       for distance, item in sorted(nearby_active, key=lambda pair: pair[0])]),
                         use_container_width=True, hide_index=True)
        else:
            st.info("No open, non-sensitive incidents are listed nearby.")
    else:
        st.info("Nearby searches need a device or saved location. The report form below can also use a manual coordinate.")

    st.divider()
    st.subheader("Submit an incident report")
    st.write("Citizen reports are shown separately from official warnings. Use the actual occurrence location; coordinates are required for the map.")
    confirmation = st.session_state.pop("sensitive_report_confirmation", None)
    if confirmation:
        st.success(f"Sensitive report {confirmation['id']} stored with a generic summary. The typed details were cleared from this session; no helpline was contacted.")
        _show_emergency_contacts(confirmation["type"])
        _show_nearby_searches(confirmation["type"], confirmation["coordinates"])
    device_coordinates = st.session_state.get("device_coordinates")
    location_options = (["Use current device location"] if device_coordinates else []) + ["Enter coordinates manually"]
    report_location_source = st.radio("Incident location", location_options, horizontal=True, key="report_location_source")
    if not device_coordinates:
        st.info("To use GPS, open Home and press the location button first. Otherwise enter the incident coordinates below.")
    with st.form("incident_report_form"):
        report = st.text_area("What is happening?", placeholder="Describe the hazard, people affected, and immediate danger.", height=100, key="incident_report_text")
        location_cols = st.columns(3)
        report_state = location_cols[0].selectbox("State / UT", ["Select state / UT"] + INDIAN_STATES)
        report_district = location_cols[1].text_input("District", placeholder="District where incident occurred")
        report_location = location_cols[2].text_input("Locality / landmark", placeholder="Street, village, landmark")
        if report_location_source == "Use current device location":
            report_lat, report_lon = device_coordinates
            st.caption(f"Incident coordinates will be saved from this device: {report_lat:.5f}, {report_lon:.5f}. GPS gives coordinates only; verify the state and district above.")
        else:
            coordinate_cols = st.columns(2)
            report_lat = coordinate_cols[0].number_input("Incident latitude", min_value=6.0, max_value=38.0, value=17.385, format="%.5f")
            report_lon = coordinate_cols[1].number_input("Incident longitude", min_value=68.0, max_value=98.0, value=78.4867, format="%.5f")
            st.caption("Manual coordinates are required when GPS is not selected. Check the map pin location before submitting.")
        submitted = st.form_submit_button("Submit citizen report", type="primary")
    selected_report_type = help_hazard
    if selected_report_type in config.SENSITIVE_INCIDENT_TYPES:
        st.warning("Sensitive report: do not include names or unnecessary intimate details. It is stored locally with a generic summary, bypasses AI and public notifications, and is not sent to police or a helpline. Call an official service directly for help.")
    if submitted:
        if (not report.strip() or report_state == "Select state / UT" or not report_district.strip()
                or not report_location.strip()):
            st.error("Describe the incident and enter its state, district, and locality.")
        else:
            context = {"state": report_state, "district": report_district.strip(),
                       "location": f"{report_location.strip()}, {report_district.strip()}, {report_state}",
                       "coordinates": [report_lat, report_lon]}
            if help_hazard:
                context["hazard_type"] = help_hazard
            with st.spinner("Checking active official alerts and coordinating responders..."):
                output = c.handle_report(report.strip(), context, live_alerts)
            if output.get("duplicate"):
                st.warning(f"Merged as a duplicate of {output['verification']['duplicate_of']}.")
                _show_emergency_contacts(output["incident"]["type"])
            elif output.get("held"):
                if output.get("sensitive"):
                    st.warning("Sensitive report saved locally with details withheld. No responder or helpline has been contacted.")
                    _show_emergency_contacts(output["incident"]["type"])
                    _show_nearby_searches(output["incident"]["type"], output["incident"]["coords"])
                    st.session_state["sensitive_report_confirmation"] = {
                        "id": output["incident"]["id"], "type": output["incident"]["type"],
                        "coordinates": output["incident"]["coords"],
                    }
                    st.session_state["clear_sensitive_report_text"] = True
                    st.rerun()
                else:
                    st.warning("Held for dispatcher review; this citizen report is not an official warning.")
                    _show_emergency_contacts(output["incident"]["type"])
            else:
                incident_type = output["incident"]["type"]
                st.success(f"Citizen incident {output['incident']['id']} · {HAZARD_LABELS.get(incident_type, incident_type)} · confidence {output['verification']['confidence']:.0%}")
                _show_emergency_contacts(incident_type)
                for reason in output["verification"]["reasons"]:
                    st.write(f"· {reason}")
                if output["verification"].get("official_alert_ids"):
                    st.warning("Cross-verified with active official alert(s): " + ", ".join(output["verification"]["official_alert_ids"]))
                if output["verification"].get("preposition_recommendation"):
                    st.info("Dispatcher recommendation · " + output["verification"]["preposition_recommendation"])
                left, right = st.columns(2)
                left.write("**Assigned resources**")
                left.dataframe(pd.DataFrame(output["resources"]["allocated"]), use_container_width=True)
                right.write("**Assigned volunteers**")
                right.dataframe(pd.DataFrame(output["volunteers"]["assigned"]), use_container_width=True)

with tab_incidents:
    st.subheader("National incident map")
    selected_sources = st.multiselect("Map layers", ["Official alert", "Citizen report"], default=["Official alert", "Citizen report"])
    map_alerts = [item for item in filtered_alerts if "Official alert" in selected_sources]
    map_incidents = [item for item in active_incidents if "Citizen report" in selected_sources
                     and (selected_state == "All states / UTs" or item.get("state") == selected_state)
                     and (not district_query or district_query.casefold() in f"{item.get('district', '')} {item.get('location', '')}".casefold())]
    map_points = _map_rows(map_alerts, map_incidents)
    st.caption("Map legend · Red/orange/yellow/green points: official IMD alert severity · Blue points: citizen reports. CAP polygon locations are approximate centroids.")
    if map_points:
        layer = pdk.Layer("ScatterplotLayer", data=map_points, get_position="[longitude, latitude]",
                           get_fill_color="color", get_radius="radius", pickable=True,
                           radius_min_pixels=5, radius_max_pixels=18, opacity=0.8)
        st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=22.5, longitude=79.0, zoom=4.0),
                                 layers=[layer], tooltip={"text": "{source}\n{label}"}), use_container_width=True)
    else:
        st.info("No active map points match the current filters.")

    st.subheader("Incident confidence and response history")
    if visible_incidents:
        incident_rows = [{"ID": item["id"], "Source": item.get("source", "Citizen report"),
                          "State": item.get("state", ""), "District": item.get("district", ""),
                          "Location": item["location"], "Coordinates": f"{item['coords'][0]:.5f}, {item['coords'][1]:.5f}",
                          "Hazard": item["type"].replace("_", " ").title(), "Severity": item["severity"],
                          "Reports": item.get("report_count", 1), "Confidence": item["confidence"],
                          "Status": item["status"].replace("_", " ").title(), "Reported": item["created_at"]}
                         for item in visible_incidents]
        st.dataframe(pd.DataFrame(incident_rows), use_container_width=True, hide_index=True)
        st.subheader("Nearby help and safety warnings")
        for incident in sorted(visible_incidents, key=lambda item: item["created_at"], reverse=True):
            label = f"{incident['id']} · {incident['type'].replace('_', ' ').title()} · {incident.get('district') or incident['location']} · {incident['status'].replace('_', ' ').title()}"
            with st.expander(label):
                st.write(f"**Saved incident location:** {incident['location']} · {incident['coords'][0]:.5f}, {incident['coords'][1]:.5f}")
                _show_emergency_contacts(incident["type"])
                _show_nearby_searches(incident["type"], incident["coords"])
                place = {"state": incident.get("state", ""), "district": incident.get("district", ""),
                         "coordinates": incident["coords"]}
                related_alerts = [alert for alert in live_alerts if matches_saved_location(alert, place)]
                linked_ids = set(incident.get("official_alert_ids", []))
                related_alerts = [alert for alert in related_alerts if alert.get("status") == "Active"
                                  or alert.get("id") in linked_ids]
                if related_alerts:
                    for alert in related_alerts:
                        st.warning(f"{alert['color']} official warning · {alert['title']} · {alert.get('area', '')} · expires {alert.get('expires') or 'not stated'}")
                        if alert.get("instruction"):
                            st.write(alert["instruction"])
                else:
                    st.info("No currently active official IMD warning matched this incident location. This does not verify the citizen report.")

                hazard = incident["type"].replace("_", " ")
                steps = GUIDANCE.get(hazard, GUIDANCE["other"])
                if incident["severity"] >= 4:
                    st.error(f"High-priority citizen report · call {HELPLINE['number']} if anyone faces immediate danger.")
                st.markdown("**Safety guidance**")
                st.write(steps["during"])

                help_points = nearest_help(s.get("facilities", []), {"coordinates": incident["coords"]}, limit=10)
                help_points = [point for point in help_points if point["distance_km"] <= 100]
                st.markdown("**Nearby help points**")
                if help_points:
                    st.dataframe(pd.DataFrame([{"Name": point["name"], "Type": point["kind"],
                                                "Distance km": point["distance_km"], "Data status": point["source"]}
                                               for point in help_points]), use_container_width=True, hide_index=True)
                    if any(point["source"].startswith("SIMULATED") for point in help_points):
                        st.warning("Some nearby help points are simulated demo entries. Confirm locations before travelling.")
                else:
                    st.warning(f"No help point is listed within 100 km. Directory coverage is incomplete; call {HELPLINE['number']} for urgent assistance.")

        incident_id = st.selectbox("Incident timeline", [item["id"] for item in visible_incidents])
        selected_incident = next(item for item in visible_incidents if item["id"] == incident_id)
        st.write(f"**Confidence:** {selected_incident['confidence']:.0%} · "
                 f"**Why:** {'; '.join(selected_incident.get('verification_reasons', [])) or 'No additional verification detail'}")
        if selected_incident.get("preposition_recommendation"):
            st.info(selected_incident["preposition_recommendation"])
        if selected_incident["status"] == "unverified":
            if selected_incident["type"] in config.SENSITIVE_INCIDENT_TYPES:
                st.warning("This sensitive case is not eligible for automated dispatch. Contact a listed official service directly; this app does not contact them.")
                _show_emergency_contacts(selected_incident["type"])
            elif st.button("Approve and dispatch", type="primary"):
                result = c.approve(incident_id)
                if result.get("error"):
                    st.error(result["error"])
                else:
                    st.success("Dispatcher approved; response dispatched.")
                    st.rerun()
        elif selected_incident["status"] in ("dispatched", "en_route", "on_scene"):
            if selected_incident["status"] in ("dispatched", "en_route"):
                next_status = "en_route" if selected_incident["status"] == "dispatched" else "on_scene"
                label = "Mark team en route" if next_status == "en_route" else "Mark team on scene"
                if st.button(label):
                    c.advance_status(incident_id, next_status)
                    st.rerun()
            resolution = st.text_input("Resolution notes", key="resolution_notes")
            if st.button("Resolve incident"):
                c.resolve(incident_id, resolution)
                st.rerun()
        for event in selected_incident.get("timeline", []):
            st.write(f"{event['at']} · {event['event']}")
    else:
        st.info("Citizen incident history is empty. Official warnings are maintained separately from citizen reports.")

    st.subheader("Recurring patterns and area risk indicator")
    patterns = {}
    incident_history = s["incidents"]
    alert_history = s.get("alert_history", [])
    for item in visible_incidents:
        key = (item.get("state", "Unknown"), item.get("district") or item.get("location", "Unknown"), item["type"], "Citizen report")
        patterns[key] = patterns.get(key, 0) + 1
    for item in s.get("alert_history", []):
        key = (item.get("state") or "Unknown", item.get("district") or item.get("area") or "Unknown", item["hazard"], "Official alert")
        patterns[key] = patterns.get(key, 0) + 1
    repeated = [(key, count) for key, count in patterns.items() if count >= 2]
    if repeated:
        for (state_name, area_name, hazard, source), count in sorted(repeated, key=lambda row: -row[1]):
            st.warning(f"{area_name}, {state_name}: {count} recorded {source.lower()} entries for {hazard}. This is a historical signal, not a forecast.")
    elif patterns:
        st.info("No repeated district-and-hazard pattern in saved history yet.")
    else:
        st.info("Risk indicators will appear as official alert history and citizen incidents accumulate.")
    risk_rows = area_risk_scores(live_alerts, alert_history, visible_incidents)
    if risk_rows:
        st.dataframe(pd.DataFrame(risk_rows), use_container_width=True, hide_index=True)
        risk_chart = pd.DataFrame(risk_rows)
        risk_chart["area"] = risk_chart["district"] + " · " + risk_chart["state"]
        st.bar_chart(risk_chart.set_index("area")[["risk_score"]])
    st.caption("Area risk is a transparent heuristic from current official alerts, citizen reports, and saved history. It is not an official district rating or forecast.")

with tab_guidance:
    st.subheader("Emergency contacts")
    _show_emergency_contacts(None, show_all=True)
    st.caption("Women Helpline availability may vary by state. Call 112 if a number does not connect or danger is immediate.")
    guidance_hazard = st.selectbox("Hazard guidance", sorted(GUIDANCE))
    steps = GUIDANCE[guidance_hazard]
    before, during, after = st.columns(3)
    before.subheader("Before")
    before.write(steps["before"])
    during.subheader("During")
    during.write(steps["during"])
    after.subheader("After")
    after.write(steps["after"])
    st.caption("General safety information only. Follow current instructions from local authorities and emergency responders.")
    st.subheader("Nearest help finder")
    if s.get("saved_locations"):
        help_location_label = st.selectbox("Search from saved location", [item["name"] for item in s["saved_locations"]], key="help_location")
        help_location = next(item for item in s["saved_locations"] if item["name"] == help_location_label)
        facilities = [item for item in s.get("facilities", [])
                      if item.get("state", "").casefold() == help_location["state"].casefold()
                      and item.get("district", "").casefold() == help_location["district"].casefold()]
        nearest = nearest_help(facilities, help_location)
        if nearest:
            st.dataframe(pd.DataFrame([{"Facility": item["name"], "Type": item["kind"],
                                        "Distance km": item["distance_km"], "Data status": item["source"]}
                                       for item in nearest]), use_container_width=True, hide_index=True)
        else:
            st.info("No facilities are listed for this district. Check official local directories or call 112.")
        if any(item.get("source", "").startswith("SIMULATED") for item in facilities):
            st.warning("Facility listings marked SIMULATED are placeholders, not verified service locations.")
    else:
        st.info("Save a location to find listed help points nearby. The bundled directory is only a simulated Hyderabad demo.")

with tab_assets:
    st.dataframe(pd.DataFrame(s["resources"]), use_container_width=True)
    st.dataframe(pd.DataFrame(s["volunteers"]), use_container_width=True)

with tab_memory:
    question = st.text_input("Ask emergency memory", "What resources worked best for floods in Kukatpally?")
    if st.button("Ask memory"):
        st.write(c.ask(question))