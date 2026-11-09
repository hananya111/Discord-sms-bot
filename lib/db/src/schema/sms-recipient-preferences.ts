import { createInsertSchema } from "drizzle-zod";
import { pgTable, text, timestamp, varchar } from "drizzle-orm/pg-core";
import { z } from "zod/v4";

export const smsRecipientPreferencesTable = pgTable(
  "sms_recipient_preferences",
  {
    phoneHash: varchar("phone_hash", { length: 64 }).primaryKey(),
    status: text("status", { enum: ["opted_in", "opted_out"] }).notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow()
      .$onUpdate(() => new Date()),
  },
);

export const insertSmsRecipientPreferenceSchema = createInsertSchema(
  smsRecipientPreferencesTable,
).omit({ updatedAt: true });

export type InsertSmsRecipientPreference = z.infer<
  typeof insertSmsRecipientPreferenceSchema
>;
export type SmsRecipientPreference =
  typeof smsRecipientPreferencesTable.$inferSelect;
