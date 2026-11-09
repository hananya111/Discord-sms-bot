import asyncio
import logging
import os
import re
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands
from twilio.rest import Client

from discord_ui import SmsPanelView
from sms_accounting import (
    adjust_user_credits,
    finish_sms_attempt,
    get_credit_balance,
    reserve_sms_attempt,
)

LOGGER = logging.getLogger("discord_sms_bot")
MAX_MESSAGE_LENGTH = 500
E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")


@dataclass(frozen=True)
class Settings:
    discord_token: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_number: str

    @classmethod
    def from_environment(cls) -> "Settings":
        required = (
            "DISCORD_TOKEN",
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_NUMBER",
        )
        missing = [key for key in required if not os.getenv(key)]
        if missing:
            names = ", ".join(missing)
            raise RuntimeError(f"Missing required Replit Secrets: {names}")

        raw_twilio_number = os.environ["TWILIO_NUMBER"].strip()
        normalized_twilio_number = re.sub(r"[\s().-]", "", raw_twilio_number)
        settings = cls(
            discord_token=os.environ["DISCORD_TOKEN"],
            twilio_account_sid=os.environ["TWILIO_ACCOUNT_SID"],
            twilio_auth_token=os.environ["TWILIO_AUTH_TOKEN"],
            twilio_number=normalized_twilio_number,
        )
        if not E164_PATTERN.fullmatch(settings.twilio_number):
            raise RuntimeError(
                "TWILIO_NUMBER must include a country code in E.164 format, "
                "such as +972501234567."
            )
        return settings


def build_bot(settings: Settings) -> commands.Bot:
    twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    bot = commands.Bot(
        command_prefix="!",
        intents=discord.Intents.default(),
    )
    synced_commands = False

    @bot.event
    async def on_ready() -> None:
        nonlocal synced_commands
        if not synced_commands:
            await bot.tree.sync()
            synced_commands = True
        LOGGER.info("Bot connected as %s", bot.user)

    async def submit_sms(
        interaction: discord.Interaction,
        number: str,
        message: str,
        consent: bool,
    ) -> None:
        recipient = number.strip()
        body = message.strip()

        if not consent:
            await interaction.response.send_message(
                "נדרשת הסכמה מפורשת של הנמען.",
                ephemeral=True,
            )
            return

        if not E164_PATTERN.fullmatch(recipient):
            await interaction.response.send_message(
                "יש להזין מספר בפורמט בינלאומי, למשל ‎+972501234567.",
                ephemeral=True,
            )
            return

        if not body:
            await interaction.response.send_message(
                "יש להזין תוכן להודעה.",
                ephemeral=True,
            )
            return

        if len(body) > MAX_MESSAGE_LENGTH:
            await interaction.response.send_message(
                f"ההודעה ארוכה מדי. אפשר לשלוח עד {MAX_MESSAGE_LENGTH} תווים.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            reservation = await asyncio.to_thread(
                reserve_sms_attempt,
                str(interaction.user.id),
                recipient,
                consent,
            )
        except Exception as error:
            LOGGER.error(
                "Could not reserve SMS send (exception=%s)",
                type(error).__name__,
            )
            await interaction.followup.send(
                "לא ניתן לאמת הסרה, קרדיטים או מגבלות שליחה. ההודעה לא נשלחה.",
                ephemeral=True,
            )
            return

        if not reservation.allowed:
            messages = {
                "opted_out": "המספר ביקש להסיר את עצמו. ההודעה לא נשלחה.",
                "no_credits": "אין מספיק קרדיטים. פנו לבעל הבוט לקבלת קרדיטים.",
                "user_hourly_limit": "הגעתם למגבלת 3 הודעות בשעה למשתמש.",
                "user_daily_limit": "הגעתם למגבלת 10 הודעות ב-24 שעות למשתמש.",
                "recipient_hourly_limit": (
                    "אפשר לשלוח למספר הזה הודעה אחת בלבד בכל שעה."
                ),
                "consent_required": "נדרשת הסכמה מפורשת של הנמען.",
                "invalid_number": "יש להזין מספר בפורמט E.164.",
            }
            await interaction.followup.send(
                messages.get(
                    reservation.reason,
                    "ההודעה לא אושרה לשליחה.",
                ),
                ephemeral=True,
            )
            return

        assert reservation.attempt_id is not None
        try:
            result = await asyncio.to_thread(
                twilio_client.messages.create,
                body=body,
                from_=settings.twilio_number,
                to=recipient,
            )
        except Exception as error:
            try:
                await asyncio.to_thread(
                    finish_sms_attempt,
                    reservation.attempt_id,
                    "uncertain",
                )
            except Exception as update_error:
                LOGGER.error(
                    "Could not update uncertain SMS attempt (exception=%s)",
                    type(update_error).__name__,
                )
            LOGGER.error(
                "Twilio SMS request failed (exception=%s, status=%s)",
                type(error).__name__,
                getattr(error, "status", "unknown"),
            )
            await interaction.followup.send(
                "Twilio לא אישר את מצב ההודעה. הקרדיט ומגבלות השליחה נשמרים "
                "כדי למנוע הודעה כפולה; בדקו את Twilio לפני ניסיון נוסף.",
                ephemeral=True,
            )
            return

        try:
            await asyncio.to_thread(
                finish_sms_attempt,
                reservation.attempt_id,
                "submitted",
                result.sid,
            )
        except Exception as error:
            LOGGER.error(
                "Could not finalize submitted SMS attempt (exception=%s)",
                type(error).__name__,
            )

        await interaction.followup.send(
            f"ההודעה הועברה ל-Twilio. מזהה: `{result.sid}`. "
            f"יתרת קרדיטים: **{reservation.remaining_credits}**.",
            ephemeral=True,
        )

    @bot.tree.command(name="sms", description="שליחת הודעת SMS אחת")
    @app_commands.describe(
        number="מספר הנמען בפורמט בינלאומי, למשל ‎+972501234567",
        message="תוכן ההודעה, עד 500 תווים",
        consent="אישור שהנמען הסכים לקבל את ההודעה",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def sms(
        interaction: discord.Interaction,
        number: str,
        message: str,
        consent: bool,
    ) -> None:
        await submit_sms(interaction, number, message, consent)

    @bot.tree.command(
        name="sms-panel",
        description="פתיחת ממשק ה-SMS והקרדיטים",
    )
    async def sms_panel(interaction: discord.Interaction) -> None:
        try:
            balance = await asyncio.to_thread(
                get_credit_balance,
                str(interaction.user.id),
            )
            balance_text = str(balance)
        except Exception as error:
            LOGGER.error(
                "Could not load SMS panel balance (exception=%s)",
                type(error).__name__,
            )
            balance_text = "לא זמין"

        embed = discord.Embed(
            title="SMS Studio",
            description=(
                "שליחת SMS יחיד למקבל שנתן הסכמה. אין שליחה לרשימות או "
                "שליחה אוטומטית."
            ),
            color=discord.Color.from_rgb(73, 68, 220),
        )
        embed.add_field(
            name="הקרדיטים שלי",
            value=balance_text,
            inline=True,
        )
        embed.add_field(
            name="מגבלות",
            value=(
                "קרדיט אחד להודעה · עד 3 למשתמש בשעה · "
                "עד 10 למשתמש ב-24 שעות · אחת למספר בשעה"
            ),
            inline=False,
        )
        embed.set_footer(
            text="שליחה דורשת אישור הסכמה; בקשות הסרה נחסמות אוטומטית."
        )
        await interaction.response.send_message(
            embed=embed,
            view=SmsPanelView(bot, submit_sms),
            ephemeral=True,
        )

    credits_group = app_commands.Group(
        name="credits",
        description="בדיקה וניהול של קרדיטי SMS",
    )

    @credits_group.command(
        name="balance",
        description="בדיקת יתרת הקרדיטים שלך",
    )
    async def credits_balance(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            balance = await asyncio.to_thread(
                get_credit_balance,
                str(interaction.user.id),
            )
        except Exception as error:
            LOGGER.error(
                "Credit balance lookup failed (exception=%s)",
                type(error).__name__,
            )
            await interaction.followup.send(
                "לא ניתן לבדוק את יתרת הקרדיטים כרגע.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"יתרת הקרדיטים שלך: **{balance}**.",
            ephemeral=True,
        )

    @credits_group.command(
        name="grant",
        description="הקצאת קרדיטים למשתמש",
    )
    @app_commands.checks.is_owner()
    async def credits_grant(
        interaction: discord.Interaction,
        user: discord.User,
        amount: app_commands.Range[int, 1, 100_000],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            balance = await asyncio.to_thread(
                adjust_user_credits,
                str(interaction.user.id),
                str(user.id),
                amount,
                "grant",
            )
        except Exception as error:
            LOGGER.error(
                "Owner credit grant failed (exception=%s)",
                type(error).__name__,
            )
            await interaction.followup.send(
                "לא ניתן לעדכן את הקרדיטים כרגע.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"הוקצו {amount} קרדיטים ל-{user.mention}. "
            f"יתרה חדשה: **{balance}**.",
            ephemeral=True,
        )

    @credits_group.command(
        name="set",
        description="קביעת יתרת קרדיטים למשתמש",
    )
    @app_commands.checks.is_owner()
    async def credits_set(
        interaction: discord.Interaction,
        user: discord.User,
        amount: app_commands.Range[int, 0, 100_000],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            balance = await asyncio.to_thread(
                adjust_user_credits,
                str(interaction.user.id),
                str(user.id),
                amount,
                "set",
            )
        except Exception as error:
            LOGGER.error(
                "Owner credit reset failed (exception=%s)",
                type(error).__name__,
            )
            await interaction.followup.send(
                "לא ניתן לעדכן את הקרדיטים כרגע.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"יתרת הקרדיטים של {user.mention} נקבעה ל-**{balance}**.",
            ephemeral=True,
        )

    bot.tree.add_command(credits_group)

    @bot.tree.command(name="test", description="בדיקת פעילות הבוט")
    async def test(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "הבוט פועל.",
            ephemeral=True,
        )

    @bot.tree.command(name="help", description="הצגת עזרה")
    async def help_command(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "**פקודות הבוט:**\n"
            "`/sms` — שליחת הודעה אחת לנמען שנתן הסכמה מפורשת "
            "(למנהלי שרת בלבד)\n"
            "`/test` — בדיקת פעילות הבוט\n"
            "`/help` — הצגת העזרה",
            ephemeral=True,
        )

    @bot.tree.error
    async def handle_app_command_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "רק מנהלי שרת יכולים להשתמש בפקודה הזו."
        elif isinstance(error, app_commands.CheckFailure):
            message = "רק בעל הבוט יכול להשתמש בפקודה הזו."
        else:
            LOGGER.error("Discord command failed (exception=%s)", type(error).__name__)
            message = "הפקודה נכשלה. נסו שוב מאוחר יותר."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    return bot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.from_environment()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error

    bot = build_bot(settings)
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
