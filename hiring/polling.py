"""Development-only MAX long polling. Production must use HTTPS webhook."""
import argparse
import json
import logging
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .config import Config
from .db import Base, connect
from .bot import process_event, start_worker
from .max_client import tls_context


class PollCursor(Base):
    __tablename__ = "hiring_poll_cursor"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    marker: Mapped[str] = mapped_column(String(64))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Check token and subscriptions, do not consume events")
    args = parser.parse_args()
    load_dotenv(".env.hiring")
    config = Config()
    config.validate()
    if not config.bot_token:
        raise SystemExit("Set MAX_BOT_TOKEN in .env.hiring")
    if config.production and not args.check:
        raise SystemExit("Production requires webhook, not polling")
    # HTTP debug logging may contain request headers. Never enable it here.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with httpx.Client(base_url=config.max_api_url, headers={"Authorization": config.bot_token}, verify=tls_context(config.max_api_url), timeout=40) as api:
        info = api.get("/me")
        if info.status_code != 200:
            raise SystemExit(f"MAX /me HTTP {info.status_code}: check token (never printed)")
        bot = info.json()
        config.bot_name = bot.get("username") or str(bot["user_id"])
        subscriptions = api.get("/subscriptions")
        subscriptions.raise_for_status()
        count = len(subscriptions.json().get("subscriptions", []))
        print(json.dumps({"bot": config.bot_name, "url": f"https://max.ru/{config.bot_name}", "webhooks": count}, ensure_ascii=False), flush=True)
        if args.check:
            return
        if count:
            raise SystemExit("Bot already has a webhook. Polling was not started; existing subscriptions were not changed.")
        if not config.employer_code:
            raise SystemExit("Set HIRING_EMPLOYER_CODE before starting polling")
        Path("data").mkdir(exist_ok=True)
        engine, factory = connect(config.database_url)
        worker = start_worker(factory, config)
        print("MAX polling started; waiting for /start. Keep this process running.", flush=True)
        try:
            while True:
                with factory() as db:
                    cursor = db.get(PollCursor, str(bot["user_id"]))
                    marker = cursor.marker if cursor else None
                params = {"timeout": 25, "limit": 50, "types": "bot_started,message_created"}
                if marker is not None:
                    params["marker"] = marker
                try:
                    result = api.get("/updates", params=params)
                    if result.status_code in (401, 403, 405):
                        raise SystemExit(f"Polling stopped: MAX HTTP {result.status_code}")
                    result.raise_for_status()
                    payload = result.json()
                    for event in payload.get("updates", []):
                        process_event(factory, event, config)
                    if payload.get("marker") is not None:
                        with factory() as db:
                            db.merge(PollCursor(id=str(bot["user_id"]), marker=str(payload["marker"])))
                            db.commit()
                    if payload.get("updates"):
                        print(f"Processed {len(payload['updates'])} update(s)", flush=True)
                except (httpx.HTTPError, ValueError):
                    print("MAX temporarily unavailable; retrying in 5 seconds", flush=True)
                    time.sleep(5)
        except KeyboardInterrupt:
            print("Polling stopped")
        finally:
            worker[0].set()
            worker[1].join(timeout=15)
            engine.dispose()


if __name__ == "__main__":
    main()
