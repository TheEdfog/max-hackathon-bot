"""Export the current FastAPI contract without touching the running bot's database."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# main creates the app at import time. Isolate that side effect from user data.
os.environ["HIRING_DATABASE_URL"] = "sqlite://"
os.environ["HIRING_ENV"] = "development"
os.environ["MAX_BOT_TOKEN"] = ""
os.environ["HIRING_SECRET"] = "openapi-export-only-not-a-deployment-secret"

from hiring.main import app  # noqa: E402


if __name__ == "__main__":
    target = ROOT / "openapi.json"
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(app.openapi()['paths'])} paths to {target.name}")
