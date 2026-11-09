---
name: Twilio opt-out replies
description: Behavior for Twilio Advanced Opt-Out webhooks and stored recipient preferences.
---

When an inbound webhook includes Twilio's `OptOutType`, persist the preference but return empty TwiML; Twilio has already sent its confirmation and another reply would duplicate it. Keep `/sms` blocked for opted-out recipients, including when the database check fails. A `START` event clears the block, but the Discord command still requires the admin's explicit consent confirmation.

**Why:** Twilio Advanced Opt-Out sends its own keyword confirmation, and duplicate replies are undesirable; failed preference checks must never allow a message through.

**How to apply:** Preserve this behavior when changing the inbound webhook or `/sms` flow.
