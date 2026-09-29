import base64

import config
from guidance import GUIDANCE, HELPLINE


class MultimodalUnavailable(RuntimeError):
    pass


HAZARD_TERMS = {
    "maritime emergency": ("ship", "vessel", "man overboard", "boat capsized", "at sea"),
    "earthquake": ("earthquake", "tremor", "shaking building"),
    "cyclone": ("cyclone", "storm surge"),
    "flood": ("flood", "water entering", "inundation", "submerged"),
    "heavy rain": ("heavy rain", "downpour", "cloudburst", "waterlogging"),
    "heatwave": ("heatwave", "heat wave", "heat stroke"),
    "tsunami": ("tsunami",),
    "wildfire": ("wildfire", "forest fire"),
    "landslide": ("landslide", "landslip"),
    "road blockage": ("road blocked", "road blockage", "tree fell", "landslide"),
    "accident": ("accident", "collision", "crash", "injured on road"),
    "medical emergency": ("unconscious", "bleeding", "heart attack", "not breathing", "injured"),
    "power outage": ("power outage", "power cut", "downed wire", "transformer"),
}
SENSITIVE_TERMS = {
    "sexual_assault": ("rape", "sexual assault", "molest", "sexually assaulted"),
    "kidnapping": ("kidnap", "abduct", "missing child", "taken by force"),
    "child_labour": ("child labour", "child labor", "forced child work"),
    "abuse": ("domestic violence", "abuse at home", "being beaten", "physical abuse"),
}


def detect_hazard(text):
    content = (text or "").casefold()
    for hazard, terms in HAZARD_TERMS.items():
        if any(term in content for term in terms):
            return hazard
    return "other"


def offline_reply(text):
    hazard = detect_hazard(text)
    steps = GUIDANCE.get(hazard, GUIDANCE["other"])
    return (f"Possible hazard: {hazard.title()}. I can provide general guidance, but this report has not been verified.\n\n"
            f"Now: {steps['during']}\n\n"
            f"If anyone is in immediate danger, call {HELPLINE['number']}. Share the exact location with responders.")


def detect_sensitive(text):
    content = (text or "").casefold()
    return next((kind for kind, terms in SENSITIVE_TERMS.items()
                 if any(term in content for term in terms)), None)


def private_safety_reply(kind, voice_transcribed=False):
    contacts = {"sexual_assault": "112 or Women Helpline 181; for a child, 1098",
                "kidnapping": "112; for a child, Child Helpline 1098",
                "child_labour": "Child Helpline 1098 or 112",
                "abuse": "112; Women Helpline 181 where available; for a child, 1098"}
    if voice_transcribed:
        privacy_note = "The voice recording was sent to Groq for transcription before sensitive content was recognized; its transcript was not saved in chat history."
    else:
        privacy_note = "This typed sensitive disclosure was handled locally and omitted from chat history."
    return (f"{privacy_note} Do not use voice uploads for sensitive cases. "
            f"If anyone is in immediate danger, call {contacts[kind]} directly. This app does not contact them for you.")


class EmergencyChatbot:
    def __init__(self, llm):
        self.llm = llm

    def transcribe(self, audio_bytes, filename):
        if not self.llm.available:
            raise MultimodalUnavailable("Voice transcription requires GROQ_API_KEY.")
        response = self.llm.client.audio.transcriptions.create(
            file=(filename, audio_bytes), model=config.GROQ_AUDIO_MODEL, response_format="json")
        return response.text.strip()

    def reply(self, text, image_bytes=None, image_mime="image/jpeg", history=None, voice_transcribed=False):
        sensitive_type = detect_sensitive(text)
        if sensitive_type:
            return private_safety_reply(sensitive_type, voice_transcribed)
        if image_bytes and not self.llm.available:
            raise MultimodalUnavailable("Photo analysis requires GROQ_API_KEY. You can still ask about the image in text.")
        if not self.llm.available:
            return offline_reply(text)

        system = (
            "You are a cautious emergency safety assistant for people in India. Analyze the user's text and, if provided, "
            "the attached image. Clearly distinguish visible observations from uncertain guesses. Never claim an image "
            "proves an event or exact location. Do not diagnose injuries or tell people to take dangerous actions. Give "
            "brief practical safety guidance, ask for missing location/details when useful, and tell people to call 112 "
            "when there may be immediate danger. You are not an official warning authority; direct users to official responders."
        )
        content = [{"type": "text", "text": text or "Describe the visible emergency and give safe next steps."}]
        model = config.GROQ_MODEL
        if image_bytes:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{encoded}"}})
            model = config.GROQ_VISION_MODEL
        messages = [{"role": "system", "content": system}]
        messages.extend((history or [])[-8:])
        messages.append({"role": "user", "content": content})
        response = self.llm.client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=messages,
        )
        return response.choices[0].message.content.strip()