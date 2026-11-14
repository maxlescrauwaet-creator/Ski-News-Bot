"""Register the /ski slash commands globally (run once, and again whenever commands change).

Usage:  DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... python scripts/register_commands.py
"""
import os
import sys

import requests

COMMANDS = [{
    "name": "ski",
    "description": "Daily ski & snow news for this server",
    "dm_permission": False,
    "default_member_permissions": "32",  # Manage Server
    "options": [
        {"type": 1, "name": "setup", "description": "Choose the channel for the daily post", "options": [
            {"type": 7, "name": "channel", "description": "Channel for the daily post", "required": True, "channel_types": [0, 5]},
            {"type": 5, "name": "mention_everyone", "description": "Ping @everyone with each post (needs Mention Everyone permission)", "required": False},
        ]},
        {"type": 1, "name": "status", "description": "Show the current settings"},
        {"type": 1, "name": "stop", "description": "Stop the daily posts and delete this server's data"},
    ],
}]

app, token = os.environ["DISCORD_APP_ID"], os.environ["DISCORD_BOT_TOKEN"]
r = requests.put(f"https://discord.com/api/v10/applications/{app}/commands",
                 headers={"Authorization": f"Bot {token}"}, json=COMMANDS, timeout=30)
print(r.status_code, r.text[:300])
sys.exit(0 if r.ok else 1)
