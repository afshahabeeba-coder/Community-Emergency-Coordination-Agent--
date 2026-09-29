"""Persistent memory layer built on Hindsight (Vectorize).

retain()  -> store an experience (incident, resolution, volunteer note, ...)
recall()  -> semantic retrieval of relevant past memories
reflect() -> LLM-powered reasoning over all memories ("what usually works in Kukatpally floods?")

If the Hindsight server is unreachable we transparently fall back to a local JSON store
with keyword scoring so the demo never breaks.
"""
import json, re, datetime as dt
import config

try:
    from hindsight_client import Hindsight
except ImportError:  # pragma: no cover
    Hindsight = None


class Memory:
    def __init__(self):
        self.bank = config.HINDSIGHT_BANK_ID
        self.client = None
        self.backend = "local"
        if Hindsight:
            try:
                kw = {"base_url": config.HINDSIGHT_BASE_URL}
                if config.HINDSIGHT_API_KEY:
                    kw["api_key"] = config.HINDSIGHT_API_KEY
                self.client = Hindsight(**kw)
                self.client.recall(bank_id=self.bank, query="ping")  # connectivity probe
                self.backend = "hindsight"
            except Exception as e:
                print(f"[memory] Hindsight unavailable ({type(e).__name__}); using local fallback.")
                self.client = None

    def _load_local(self):
        return json.loads(config.LOCAL_MEMORY_FILE.read_text()) if config.LOCAL_MEMORY_FILE.exists() else []

    def _save_local(self, items):
        config.LOCAL_MEMORY_FILE.write_text(json.dumps(items, indent=1))

    @staticmethod
    def _tok(s):
        return set(re.findall(r"[a-z0-9_]+", s.lower()))

    def retain(self, content: str, context: str = "emergency-coordination") -> None:
        stamp = dt.datetime.utcnow()
        if self.client:
            try:
                self.client.retain(bank_id=self.bank, content=content, context=context, timestamp=stamp)
                return
            except Exception as e:
                print(f"[memory] retain failed, saving locally: {e}")
        items = self._load_local()
        items.append({"content": content, "context": context, "ts": stamp.isoformat()})
        self._save_local(items)

    def recall(self, query: str, k: int = 5) -> list[str]:
        if self.client:
            try:
                res = self.client.recall(bank_id=self.bank, query=query)
                results = getattr(res, "results", res) or []
                return [getattr(r, "text", str(r)) for r in results][:k]
            except Exception as e:
                print(f"[memory] recall failed, using local: {e}")
        q = self._tok(query)
        scored = []
        for it in self._load_local():
            overlap = len(q & self._tok(it["content"]))
            if overlap:
                scored.append((overlap, it["ts"], it["content"]))
        scored.sort(reverse=True)
        return [c for _, _, c in scored[:k]]

    def reflect(self, question: str) -> str:
        if self.client:
            try:
                res = self.client.reflect(bank_id=self.bank, query=question)
                return getattr(res, "text", str(res))
            except Exception as e:
                print(f"[memory] reflect failed: {e}")
        hits = self.recall(question, k=6)
        return "Relevant past records:\n- " + "\n- ".join(hits) if hits else "No relevant history yet."
