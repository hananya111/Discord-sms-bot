# Discord SMS Bot

An administrator-only Discord bot for sending consent-based, one-off SMS messages through Twilio. The workspace also retains the shared API and canvas scaffolds.

## Run & Operate

- Start the **Discord SMS Bot** workflow, or run `python discord_sms_bot.py`
- Required secrets: `DISCORD_TOKEN`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER`
- Twilio inbound webhook: `POST /api/twilio/inbound`; configure the public HTTPS URL in Twilio
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- Python 3.13, discord.py, Twilio Python SDK
- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `discord_sms_bot.py` — Discord slash commands and Twilio SMS sending
- `sms_opt_out.py` — durable database check before sending SMS
- `artifacts/api-server/src/routes/twilio-inbound.ts` — signed Twilio opt-out webhook
- `lib/db/src/schema/sms-recipient-preferences.ts` — persisted opt-in/opt-out status
- `README.md` — bot setup and operating notes
- `artifacts/api-server` — shared Express API
- `artifacts/mockup-sandbox` — canvas component previews

## Architecture decisions

- SMS sends require Discord administrator permission and explicit consent confirmation.
- Each recipient has a one-hour in-memory cooldown; restarting the bot resets it.
- Inbound Twilio opt-out requests are signature-validated and stored as SHA-256 phone hashes.
- The bot fails closed when it cannot verify the stored opt-out status.

## Product

The bot provides `/sms`, `/test`, and `/help` commands. SMS is sent only through the configured Twilio account.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
