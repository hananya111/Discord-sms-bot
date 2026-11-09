import { createHash } from "node:crypto";
import { Router, type IRouter, type Request } from "express";
import { RecordTwilioInboundSmsBody } from "@workspace/api-zod";
import { db, smsRecipientPreferencesTable } from "@workspace/db";
import twilio from "twilio";

const router: IRouter = Router();

const STOP_KEYWORDS = new Set([
  "STOP",
  "STOPALL",
  "UNSUBSCRIBE",
  "END",
  "QUIT",
  "REVOKE",
  "OPTOUT",
  "CANCEL",
  "BLOCK",
]);
const START_KEYWORDS = new Set(["START", "YES", "UNSTOP", "UNBLOCK"]);

type PreferenceStatus = "opted_in" | "opted_out";

function classifyPreference(
  optOutType: string | undefined,
  body: string,
): PreferenceStatus | null {
  const advancedOptOutType = optOutType?.trim().toUpperCase();
  if (advancedOptOutType === "STOP") return "opted_out";
  if (advancedOptOutType === "START") return "opted_in";

  const keyword = body.trim().split(/\s+/, 1)[0]?.toUpperCase();
  if (keyword && STOP_KEYWORDS.has(keyword)) return "opted_out";
  if (keyword && START_KEYWORDS.has(keyword)) return "opted_in";
  return null;
}

function getWebhookUrl(request: Request): string {
  const configuredUrl = process.env.TWILIO_WEBHOOK_URL?.trim();
  if (configuredUrl) return configuredUrl;

  const forwardedHost = request
    .get("x-forwarded-host")
    ?.split(",")[0]
    ?.trim();
  const host = forwardedHost || request.get("host");
  if (!host) throw new Error("Could not determine the public webhook host.");

  const forwardedProtocol = request
    .get("x-forwarded-proto")
    ?.split(",")[0]
    ?.trim();
  const protocol = forwardedProtocol || request.protocol;
  return `${protocol}://${host}${request.originalUrl}`;
}

router.post("/twilio/inbound", async (request, response): Promise<void> => {
  const authToken = process.env.TWILIO_AUTH_TOKEN;
  if (!authToken) {
    request.log.error("Twilio webhook validation is not configured");
    response.sendStatus(503);
    return;
  }

  const signature = request.get("x-twilio-signature");
  if (!signature) {
    response.sendStatus(403);
    return;
  }

  let validSignature = false;
  try {
    validSignature = twilio.validateRequest(
      authToken,
      signature,
      getWebhookUrl(request),
      request.body as Record<string, string>,
    );
  } catch (error) {
    request.log.warn(
      { errorType: error instanceof Error ? error.name : "unknown" },
      "Could not validate Twilio webhook signature",
    );
  }

  if (!validSignature) {
    request.log.warn("Rejected webhook with invalid Twilio signature");
    response.sendStatus(403);
    return;
  }

  const parsed = RecordTwilioInboundSmsBody.safeParse(request.body);
  if (!parsed.success) {
    request.log.warn(
      { issueCount: parsed.error.issues.length },
      "Rejected invalid Twilio inbound message",
    );
    response.status(400).send("Invalid Twilio webhook payload.");
    return;
  }

  const status = classifyPreference(
    parsed.data.OptOutType,
    parsed.data.Body,
  );
  if (status) {
    const phoneHash = createHash("sha256")
      .update(parsed.data.From, "utf8")
      .digest("hex");

    await db
      .insert(smsRecipientPreferencesTable)
      .values({ phoneHash, status })
      .onConflictDoUpdate({
        target: smsRecipientPreferencesTable.phoneHash,
        set: { status, updatedAt: new Date() },
      });

    request.log.info(
      { status },
      "Updated SMS recipient preference from Twilio webhook",
    );
  }

  // Advanced Opt-Out already sends its own confirmation message.
  response.status(200).type("application/xml").send("<Response/>");
});

export default router;
