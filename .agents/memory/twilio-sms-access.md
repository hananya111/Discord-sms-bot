---
name: Twilio SMS access
description: The Discord SMS bot uses Twilio's Python SDK with Replit Secrets instead of the Twilio integration.
---

For this project, use the Twilio Python SDK and keep its account credentials in Replit Secrets. The user dismissed the Twilio integration connection card and supplied the required values securely.

**Why:** the user chose to continue without connecting the Replit integration.

**How to apply:** When changing this bot's SMS behavior, use the existing secret-based SDK setup. Do not prompt to connect the Twilio integration again unless the user asks.
