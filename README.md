# Discord SMS Bot

A Discord slash-command bot that sends one-off SMS messages through Twilio.

## Required Replit Secrets

Add these values under **Tools → Secrets**:

- `DISCORD_TOKEN` — the Discord application’s bot token
- `TWILIO_ACCOUNT_SID` — Twilio Account SID
- `TWILIO_AUTH_TOKEN` — Twilio Auth Token
- `TWILIO_NUMBER` — Twilio sender number in E.164 format, for example `+15551234567`

`DATABASE_URL` is provided by the Replit PostgreSQL database and does not need
to be added manually.

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

## Twilio opt-out webhook

Set the sender number or Messaging Service's inbound-message webhook to
`https://<your-public-domain>/api/twilio/inbound` with HTTP `POST`. Use the
public HTTPS URL that Twilio will call. If the proxy's forwarded URL differs
from the one used in Twilio, set the non-secret `TWILIO_WEBHOOK_URL` environment
variable to that exact full URL.

The API validates `X-Twilio-Signature`, records STOP-family keywords as
opt-outs, and handles START-family keywords as opt-ins. Recipient numbers are
stored as SHA-256 hashes in the database rather than as raw phone numbers.
Advanced Opt-Out may be enabled in Twilio to provide its own confirmation
messages; the webhook returns empty TwiML to avoid duplicate confirmations.
`/sms` checks the stored preference before sending and fails closed if the
database check is unavailable. The command still requires explicit consent
confirmation after a recipient opts back in.
