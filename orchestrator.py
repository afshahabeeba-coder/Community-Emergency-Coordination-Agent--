"""Incident -> Verification -> Resource -> Volunteer -> Notification, with persistent memory."""
import datetime as dt
import state as st
import config
from llm import LLM
from memory import Memory
from agents import IncidentAgent, VerificationAgent, ResourceAgent, VolunteerAgent, NotificationAgent


class Coordinator:
    def __init__(self):
        self.llm, self.memory = LLM(), Memory()
        a = (self.llm, self.memory)
        self.incident, self.verify = IncidentAgent(*a), VerificationAgent(*a)
        self.resource, self.volunteer, self.notify = ResourceAgent(*a), VolunteerAgent(*a), NotificationAgent(*a)

    def status(self):
        return {"llm": "groq" if self.llm.available else "rules-fallback", "memory": self.memory.backend}

    def handle_report(self, report: str, location_context: dict | None = None,
                      official_alerts: list[dict] | None = None) -> dict:
        state = st.load()
        trace = []
        inc = self.incident.run(report, state, location_context);   trace.append(("Incident", f"{inc['type']} sev{inc['severity']} @ {inc['location']}"))
        inc["id"] = f"INC-{len(state['incidents']) + 1:04d}"
        inc["created_at"] = dt.datetime.utcnow().isoformat()
        ver = self.verify.run(inc, state, official_alerts);          trace.append(("Verification", f"verified={ver['verified']} conf={ver['confidence']}"))

        if ver["duplicate_of"]:
            original = next((item for item in state["incidents"] if item["id"] == ver["duplicate_of"]), None)
            if original:
                original["report_count"] = original.get("report_count", 1) + 1
                original.setdefault("timeline", []).append({"at": inc["created_at"], "event": "Corroborating report received"})
                st.save(state)
            self.memory.retain(f"Duplicate report merged into {ver['duplicate_of']}: {report}")
            return {"incident": inc, "verification": ver, "duplicate": True, "trace": trace}
        if not ver["verified"]:
            inc["status"] = "unverified"
            state["incidents"].append(self._record(inc, ver, {"allocated": [], "shortages": []}, {"assigned": []}))
            st.save(state)
            return {"incident": inc, "verification": ver, "trace": trace, "held": True,
                    "sensitive": inc["type"] in config.SENSITIVE_INCIDENT_TYPES}

        res = self.resource.run(inc, ver, state);                   trace.append(("Resource", f"{len(res['allocated'])} allocated, shortages={res['shortages']}"))
        vol = self.volunteer.run(inc, state);                       trace.append(("Volunteer", f"{len(vol['assigned'])} assigned"))
        inc["status"] = "dispatched"
        msgs = self.notify.run(inc, ver, res, vol);                 trace.append(("Notification", f"{len(msgs)} messages sent"))
        state["incidents"].append(self._record(inc, ver, res, vol))
        st.save(state)

        self.memory.retain(
            f"Incident {inc['id']}: {inc['type']} severity {inc['severity']} at {inc['location']}. {inc['summary']} "
            f"Dispatched: {[a['kind'] for a in res['allocated']]}. Volunteers: {[v['name'] for v in vol['assigned']]}. "
            f"Shortages: {res['shortages']}.", context="incident-dispatch")
        return {"incident": inc, "verification": ver, "resources": res, "volunteers": vol, "messages": msgs, "trace": trace}

    @staticmethod
    def _record(inc, ver, res, vol):
        return {"id": inc["id"], "type": inc["type"], "severity": inc["severity"], "location": inc["location"],
                "coords": inc["coords"], "summary": inc["summary"], "status": inc.get("status", "open"),
                "created_at": inc["created_at"], "confidence": ver["confidence"],
                "people_affected": inc.get("people_affected", 0), "report_count": 1,
                "state": inc.get("state", ""), "district": inc.get("district", ""),
                "source": "Citizen report", "official_alert_ids": ver.get("official_alert_ids", []),
                "preposition_recommendation": ver.get("preposition_recommendation", ""),
                "needs_human_check": ver.get("needs_human_check", False),
                "verification_reasons": ver.get("reasons", []),
                "resource_shortages": res.get("shortages", []),
                "resources": res["allocated"], "volunteers": [v["volunteer_id"] for v in vol["assigned"]],
                "volunteer_assignments": vol["assigned"],
                "timeline": [{"at": inc["created_at"], "event": f"Report received; verification confidence {ver['confidence']:.0%}"}]}

    def approve(self, incident_id: str) -> dict:
        """Approve a held report and dispatch matching resources and volunteers."""
        state = st.load()
        inc = next((item for item in state["incidents"] if item["id"] == incident_id), None)
        if not inc or inc["status"] != "unverified":
            return {"error": "incident is not awaiting approval"}
        if inc["type"] in config.SENSITIVE_INCIDENT_TYPES:
            return {"error": "Sensitive incident: use the listed official helplines; this app cannot confidentially dispatch responders."}
        verification = {"confidence": inc["confidence"], "needs_human_check": False, "reasons": inc.get("verification_reasons", [])}
        resources = self.resource.run(inc, verification, state)
        volunteers = self.volunteer.run(inc, state)
        inc["resources"] = resources["allocated"]
        inc["volunteers"] = [item["volunteer_id"] for item in volunteers["assigned"]]
        inc["volunteer_assignments"] = volunteers["assigned"]
        inc["resource_shortages"] = resources["shortages"]
        inc["status"] = "dispatched"
        inc["approved_at"] = dt.datetime.utcnow().isoformat()
        inc.setdefault("timeline", []).append({"at": inc["approved_at"], "event": "Dispatcher approved; response dispatched"})
        messages = self.notify.run(inc, verification, resources, volunteers)
        st.save(state)
        self.memory.retain(f"Dispatcher approved incident {inc['id']} ({inc['type']} at {inc['location']}) and dispatched response.", context="incident-dispatch")
        return {"incident": inc, "resources": resources, "volunteers": volunteers, "messages": messages}

    def advance_status(self, incident_id: str, next_status: str) -> dict:
        """Advance an active response through its operational stages."""
        transitions = {"dispatched": "en_route", "en_route": "on_scene"}
        state = st.load()
        inc = next((item for item in state["incidents"] if item["id"] == incident_id), None)
        if not inc or transitions.get(inc["status"]) != next_status:
            return {"error": "invalid response status transition"}
        now = dt.datetime.utcnow().isoformat()
        inc["status"] = next_status
        inc.setdefault("timeline", []).append({"at": now, "event": f"Response status updated to {next_status.replace('_', ' ')}"})
        st.save(state)
        return {"updated": incident_id, "status": next_status}

    def resolve(self, incident_id: str, notes: str = "", outcome: str = "success") -> dict:
        """Close an incident, release resources/volunteers, and write the lesson to memory."""
        state = st.load()
        inc = next((i for i in state["incidents"] if i["id"] == incident_id), None)
        if not inc or inc["status"] == "closed":
            return {"error": "not found or already closed"}
        for a in inc["resources"]:
            for r in state["resources"]:
                if r["id"] == a["resource_id"]:
                    r["available"] = min(r["quantity"], r["available"] + a["qty"])
        for v in state["volunteers"]:
            if v["id"] in inc["volunteers"]:
                v["available"] = True
                if outcome == "success":
                    v["completed"] = v.get("completed", 0) + 1
        inc["status"], inc["resolved_at"] = "closed", dt.datetime.utcnow().isoformat()
        inc.setdefault("timeline", []).append({"at": inc["resolved_at"], "event": f"Incident resolved: {notes or 'no resolution notes'}"})
        st.save(state)
        self.memory.retain(
            f"RESOLVED {inc['id']} ({inc['type']} at {inc['location']}, sev {inc['severity']}) outcome={outcome}. "
            f"Resources used: {[a['kind'] for a in inc['resources']]}. Notes: {notes or 'none'}",
            context="resolution-history")
        return {"closed": inc["id"]}

    def ask(self, question: str) -> str:
        return self.memory.reflect(question)
