import json, datetime as dt
import config


class NotificationAgent:
    """Drafts and dispatches multilingual alerts. Delivery is an adapter: the default writes to
    data/outbox.jsonl; swap `_deliver` for Twilio/WhatsApp/SMS in production."""
    name = "NotificationAgent"

    def __init__(self, llm, memory):
        self.llm, self.memory = llm, memory

    def run(self, inc: dict, ver: dict, res: dict, vol: dict) -> list[dict]:
        base = (f"{inc['type'].replace('_', ' ').title()} reported at {inc['location'].title()} "
                f"(severity {inc['severity']}/5).")
        resources = ", ".join(f"{a['qty']}x {a['name']}" for a in res["allocated"]) or "none available"
        try:
            m = self.llm.json(
                "Write concise emergency alerts (<=40 words each). Return keys: citizens_en, citizens_te (Telugu), "
                "citizens_hi (Hindi), volunteers, authorities. Include safety advice for citizens; no invented facts.",
                f"Incident: {base} {inc['summary']}\nResources dispatched: {resources}\n"
                f"Volunteers: {[v['name'] for v in vol['assigned']]}\nShortages: {res['shortages']}\n"
                f"Needs human check: {ver['needs_human_check']}")
        except Exception:
            m = {"citizens_en": f"ALERT: {base} Avoid the area and follow official instructions. Help is on the way.",
                 "volunteers": f"TASK: {base} {inc['summary']} Report to the location immediately.",
                 "authorities": f"{base} Resources: {resources}. Shortages: {res['shortages'] or 'none'}. "
                                f"Human check: {ver['needs_human_check']}"}
        out = [self._deliver("community-broadcast", k, t, inc) for k, t in m.items() if k.startswith("citizens")]
        for v in vol["assigned"]:
            out.append(self._deliver(v["phone"], "volunteer", f"{v['name']}: {m.get('volunteers', '')}", inc))
        out.append(self._deliver("emergency-control-room", "authorities", m.get("authorities", ""), inc))
        return out

    def _deliver(self, to, channel, text, inc):
        rec = {"ts": dt.datetime.utcnow().isoformat(), "incident": inc.get("id"), "to": to, "channel": channel, "text": text}
        with open(config.OUTBOX_FILE, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
