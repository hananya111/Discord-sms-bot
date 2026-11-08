# Discord SMS Bot

A Discord slash-command bot that sends one-off SMS messages through Twilio.

## Required Replit Secrets

Add these values under **Tools → Secrets**:

- `DISCORD_TOKEN` — the Discord application’s bot token
- `TWILIO_ACCOUNT_SID` — Twilio Account SID
- `TWILIO_AUTH_TOKEN` — Twilio Auth Token
- `TWILIO_NUMBER` — Twilio sender number in E.164 format, for example `+15551234567`

The bot checks for missing values at startup and never prints secret values.

## Run

Start the **Discord SMS Bot** workflow. On startup, it connects to Discord and
syncs the slash commands:

- `/sms` — administrator-only; requires an explicit consent confirmation,
  an E.164 recipient number, and a message of up to 500 characters.
- `/test` — confirms the bot is online.
- `/help` — lists the commands.

The bot allows one SMS attempt per recipient per hour while it is running.
That in-memory limit resets if the process restarts. If Twilio returns an
uncertain error, the bot keeps the cooldown and asks the administrator to check
Twilio before retrying, to reduce the chance of a duplicate message.

Only message recipients who have explicitly opted in, and honor opt-out
requests. The bot does not automatically process incoming `STOP` messages.
