import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "state.json"
SEED_FILE = DATA_DIR / "seed.json"
LOCAL_MEMORY_FILE = DATA_DIR / "local_memory.json"
OUTBOX_FILE = DATA_DIR / "outbox.jsonl"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_AUDIO_MODEL = os.getenv("GROQ_AUDIO_MODEL", "whisper-large-v3-turbo")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
HINDSIGHT_BASE_URL = os.getenv("HINDSIGHT_BASE_URL", "http://localhost:8888")
HINDSIGHT_API_KEY = os.getenv("HINDSIGHT_API_KEY", "")
HINDSIGHT_BANK_ID = os.getenv("HINDSIGHT_BANK_ID", "community-emergency")
IMD_CAP_FEED_URL = os.getenv("IMD_CAP_FEED_URL", "https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml")
RESQNET_DEMO_MODE = os.getenv("RESQNET_DEMO_MODE", "1").strip().lower() in ("1", "true", "yes")

INCIDENT_TYPES = ["flood", "cyclone", "heavy_rain", "heatwave", "earthquake", "tsunami", "wildfire",
				  "landslide", "maritime_emergency", "abuse", "kidnapping", "sexual_assault", "child_labour",
				  "water_problem", "medical_emergency", "road_blockage", "power_outage", "accident", "other"]
SENSITIVE_INCIDENT_TYPES = {"abuse", "kidnapping", "sexual_assault", "child_labour"}
