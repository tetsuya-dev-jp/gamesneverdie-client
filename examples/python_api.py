"""Run after installing gamesneverdie-client."""

import json

from gamesneverdie import Client

client = Client()
game = client.get("outer-wilds", fields=["title", "url", "release_dates", "platforms"])
print(json.dumps(game, ensure_ascii=False, indent=2))
