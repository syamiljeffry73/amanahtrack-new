"""Send a test push to every registered phone and print Expo's answer.

Run from the backend folder (venv active), using the same database the
backend uses:
    python send_test_push.py
"""

import json
import urllib.request

from sqlalchemy import select

from app.main import SessionLocal
from app.models import PushToken
from app.services.push import EXPO_PUSH_URL

with SessionLocal() as db:
    rows = db.scalars(select(PushToken)).all()

if not rows:
    raise SystemExit(
        "No phone is registered yet. Open the app, log in, allow notifications, "
        "and run this again. (Check this window uses the same database as the backend.)"
    )

print(f"{len(rows)} phone(s) registered")
messages = [
    {
        "to": r.token,
        "title": "AmanahTrack test",
        "body": "If you can see this, push works.",
        "priority": "high",
        "channelId": "alerts",
    }
    for r in rows
]
request = urllib.request.Request(
    EXPO_PUSH_URL,
    data=json.dumps(messages).encode(),
    headers={"Content-Type": "application/json", "Accept": "application/json"},
)
with urllib.request.urlopen(request, timeout=15) as resp:
    for ticket in json.load(resp)["data"]:
        print(ticket)
