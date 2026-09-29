# 🚨 Community Emergency Coordination Agent
HackwithHyderabad 3.0 · Multi-agent response coordination with official IMD CAP alerts, citizen reports, **Groq** (reasoning), and **Hindsight** (persistent memory).

```
Citizen report → IncidentAgent → VerificationAgent → ResourceAgent → VolunteerAgent → NotificationAgent
                        ▲                 ▲                ▲               ▲                  │
                        └──────────── Hindsight memory (recall / retain / reflect) ◄──────────┘
```

| Agent | Job | Uses memory for |
|---|---|---|
| Incident | Free text → type, severity, location, people affected (Groq) | Similar past incidents |
| Verification | Duplicate detection, nearby corroboration, credibility score | Historical patterns per area |
| Resource | Picks & allocates nearest boats/pumps/ambulances/etc. | "What worked last time" |
| Volunteer | Ranks by skills + distance + track record | Completed-task history |
| Notification | English/Telugu/Hindi alerts to citizens, volunteers, control room | — |

**Memory design:** Hindsight stores *semantic history* (incidents, resolutions, lessons) and answers
`reflect()` questions. Exact stock counts live in `state.json` so inventory is never "fuzzy".
Closing an incident releases resources, bumps volunteer track record, and retains a resolution
memory, so the system improves with every incident. Life-threatening (sev ≥ 4) reports are never
blocked by verification; they're dispatched and flagged for a human.

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env        # add GROQ_API_KEY (console.groq.com)
```
Hindsight (pick one):
- **Cloud:** set `HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io` and `HINDSIGHT_API_KEY`
- **Self-host:** follow the Hindsight docs (Docker, port 8888) and keep the default URL

No keys? It still runs: rule-based agents + local JSON memory fallback (the banner shows active backends).

## Emergency alert features
- **National alerts:** current public IMD CAP/RSS weather alerts, severity colors, issuing authority, affected area, timestamps, expiry, and official bulletin links.
- **Saved locations:** enter home/work/school/family state, district, and coordinates to see matching warnings and nearby citizen reports.
- **National incident map:** official alert points and citizen incidents are separate layers with separate source labels.
- **Cross-verification:** active live IMD alerts in the same district can increase a citizen report's confidence and severity; high-severity overlap creates a dispatcher pre-positioning recommendation.
- **History and area risk:** recurring entries are grouped by source, and a 0-100 heuristic score shows official alerts and citizen reports separately. It is not an official forecast.
- **Safety guidance:** hazard-specific before/during/after guidance, India emergency number 112, and a nearest-help finder.
- **Safeguarding and utilities:** abuse, kidnapping/missing-person, sexual-assault, child-labour, and water-supply incident types; call links for 112, Women Helpline 181, and Child Helpline 1098; nearby map searches for police, hospitals, One Stop Centres, child-protection services, and water offices.
- **Sensitive-report privacy:** safeguarding reports are held locally with a generic summary and bypass AI verification, memory, automated resource dispatch, and notifications. The app does not contact helplines. Map searches are external/unverified; use phone services directly for urgent help.
- **All-State reporting:** reports can target any State/UT and name a district/locality; selectable hazards include road accidents and ship/maritime emergencies.
- **Emergency chatbot:** chat with text offline; with `GROQ_API_KEY`, upload voice recordings for transcription and photos for visual hazard observations. Configure `GROQ_AUDIO_MODEL` and `GROQ_VISION_MODEL` in `.env` if needed. The assistant gives guidance only and does not verify or dispatch reports.
- **Incidents & help page:** submit any supported incident, see relevant helplines and safety guidance, search nearby services from a device/saved location, and review open non-sensitive incidents nearby.
- **Incident-matched call link:** abuse/sexual assault → 181; child labour → 1098; accident/medical → 108 (state availability varies); flood/earthquake and other disasters → 1078 (confirm local availability). 112 remains separately available for immediate danger. A user must tap the link and confirm the call on their device; routine water/electricity issues link to local utilities instead.

The configured live feed is the **IMD CAP feed**, not an all-agency feed for every Indian hazard. No active feed result is replaced with demo content. If the feed request fails, `RESQNET_DEMO_MODE=1` shows a clearly marked simulated alert; set it to `0` to disable that fallback. The Home tab can request browser geolocation after the user presses its location button and grants permission; device coordinates stay in the current session. Saved locations are the fallback when permission is denied. The bundled Hyderabad help-point directory and default form coordinates are simulated. Replace them with verified local data before operational use. Push/SMS/WhatsApp delivery and nationwide verified facility directories are not configured.

## Run
```bash
streamlit run app.py                                   # dashboard
python main.py report "Water entering homes in Kukatpally, 12 families trapped"
python main.py resolve INC-0001 --notes "2 boats + pump cleared it in 3h"
python main.py ask "What worked best for floods in Kukatpally?"
python main.py status | python main.py reset
python tests/test_pipeline.py
```

## Extending
- Real SMS/WhatsApp: replace `NotificationAgent._deliver` (Twilio etc.)
- Real geocoding: replace the gazetteer in `data/seed.json`
- Edit `data/seed.json` for your own resources & volunteers
