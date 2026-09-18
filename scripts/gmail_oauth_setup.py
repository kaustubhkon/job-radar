"""One-time local setup for Handshake/LinkedIn alert ingestion. Prints the three GMAIL_* secrets.

Before running:
  1. https://console.cloud.google.com → create a project → enable the "Gmail API".
  2. "OAuth consent screen": User type External, add yourself as a test user.
     Then click "PUBLISH APP" so the status is "In production". Apps left in "Testing" get
     refresh tokens that expire after 7 days, and alert ingestion would silently stop.
     (You'll see an "unverified app" warning when you sign in. That's expected for a personal app.)
  3. "Credentials" → Create credentials → OAuth client ID → Application type "Desktop app".
     Download the JSON as client_secret.json next to this script (it's git-ignored).

Run:
  uv run --extra gmail python scripts/gmail_oauth_setup.py [path/to/client_secret.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install the gmail extra first: uv sync --extra gmail")
        return 1
    path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("client_secret.json"))
    if not path.exists():
        print(f"{path} not found. See the instructions at the top of this script.")
        return 1
    flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    info = json.loads(path.read_text())
    client = info.get("installed") or info.get("web") or {}
    print("\nAdd these as GitHub repo secrets (Settings → Secrets and variables → Actions):\n")
    print(f"GMAIL_OAUTH_CLIENT_ID      = {client.get('client_id')}")
    print(f"GMAIL_OAUTH_CLIENT_SECRET  = {client.get('client_secret')}")
    print(f"GMAIL_REFRESH_TOKEN        = {creds.refresh_token}")
    print("\nThen set sources.gmail_alerts.enabled: true in config.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
