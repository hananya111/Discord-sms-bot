import { createInsertSchema } from "drizzle-zod";
import {
  index,
  integer,
  pgTable,
  serial,
  text,
  timestamp,
  varchar,
} from "drizzle-orm/pg-core";
import { z } from "zod/v4";

export const smsUserCreditsTable = pgTable("sms_user_credits", {
  discordUserId: varchar("discord_user_id", { length: 20 }).primaryKey(),
  balance: integer("balance").notNull().default(0),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow()
    .$onUpdate(() => new Date()),
});

export const smsSendAttemptsTable = pgTable(
  "sms_send_attempts",
  {
    id: serial("id").primaryKey(),
    discordUserId: varchar("discord_user_id", { length: 20 }).notNull(),
    recipientHash: varchar("recipient_hash", { length: 64 }).notNull(),
    status: text("status", {
      enum: ["pending", "submitted", "uncertain"],
    })
      .notNull()
      .default("pending"),
    twilioSid: varchar("twilio_sid", { length: 64 }),
    consentConfirmedAt: timestamp("consent_confirmed_at", {
      withTimezone: true,
    })
      .notNull()
      .defaultNow(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    index("sms_send_attempts_user_created_idx").on(
      table.discordUserId,
      table.createdAt,
    ),
    index("sms_send_attempts_recipient_created_idx").on(
      table.recipientHash,
      table.createdAt,
    ),
  ],
);

export const smsCreditLedgerTable = pgTable("sms_credit_ledger", {
  id: serial("id").primaryKey(),
  discordUserId: varchar("discord_user_id", { length: 20 }).notNull(),
  actorUserId: varchar("actor_user_id", { length: 20 }).notNull(),
  action: text("action", { enum: ["grant", "set", "sms_send"] }).notNull(),
  amountDelta: integer("amount_delta").notNull(),
  balanceAfter: integer("balance_after").notNull(),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

export const insertSmsUserCreditSchema = createInsertSchema(
  smsUserCreditsTable,
).omit({ updatedAt: true });
export const insertSmsSendAttemptSchema = createInsertSchema(
  smsSendAttemptsTable,
).omit({ id: true, createdAt: true, consentConfirmedAt: true });
export const insertSmsCreditLedgerSchema = createInsertSchema(
  smsCreditLedgerTable,
).omit({ id: true, createdAt: true });

export type InsertSmsUserCredit = z.infer<typeof insertSmsUserCreditSchema>;
export type SmsUserCredit = typeof smsUserCreditsTable.$inferSelect;
export type InsertSmsSendAttempt = z.infer<
  typeof insertSmsSendAttemptSchema
>;
export type SmsSendAttempt = typeof smsSendAttemptsTable.$inferSelect;
export type InsertSmsCreditLedger = z.infer<
  typeof insertSmsCreditLedgerSchema
>;
export type SmsCreditLedger = typeof smsCreditLedgerTable.$inferSelect;
