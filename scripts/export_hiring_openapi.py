"""Export the current FastAPI contract without touching the running bot's database."""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only comparison; exit 1 if the export is stale')
    args = parser.parse_args()
    target = ROOT / "openapi.json"
    if args.check:
        matches = target.is_file() and json.loads(target.read_text(encoding='utf-8')) == app.openapi()
        print('OpenAPI matches the backend' if matches else 'OpenAPI is stale; run the exporter')
        raise SystemExit(0 if matches else 1)
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(app.openapi()['paths'])} paths to {target.name}")
