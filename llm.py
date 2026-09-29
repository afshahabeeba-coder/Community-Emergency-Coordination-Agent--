import json, re
import config

try:
    from groq import Groq
except ImportError:  # pragma: no cover
    Groq = None


class LLM:
    """Thin Groq wrapper. If no key is configured, `available` is False and agents
    fall back to deterministic rules, so the system still runs offline."""

    def __init__(self):
        self.available = bool(config.GROQ_API_KEY and Groq)
        self.client = Groq(api_key=config.GROQ_API_KEY) if self.available else None

    def json(self, system: str, user: str, temperature: float = 0.1) -> dict:
        if not self.available:
            raise RuntimeError("Groq not configured")
        r = self.client.chat.completions.create(
            model=config.GROQ_MODEL,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system + "\nRespond with a single valid JSON object only."},
                      {"role": "user", "content": user}],
        )
        txt = r.choices[0].message.content
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", txt, re.S)
            return json.loads(m.group(0))

    def text(self, system: str, user: str, temperature: float = 0.3) -> str:
        if not self.available:
            raise RuntimeError("Groq not configured")
        r = self.client.chat.completions.create(
            model=config.GROQ_MODEL, temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content.strip()
