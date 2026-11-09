import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands
from twilio.rest import Client

from sms_opt_out import is_recipient_opted_out

LOGGER = logging.getLogger("discord_sms_bot")
MAX_MESSAGE_LENGTH = 500
RECIPIENT_COOLDOWN_SECONDS = 60 * 60
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
    last_attempt_by_number: dict[str, float] = {}
    pending_numbers: set[str] = set()
    rate_limit_lock = asyncio.Lock()
    synced_commands = False

    @bot.event
    async def on_ready() -> None:
        nonlocal synced_commands
        if not synced_commands:
            await bot.tree.sync()
            synced_commands = True
        LOGGER.info("Bot connected as %s", bot.user)

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
            opted_out = await asyncio.to_thread(
                is_recipient_opted_out,
                recipient,
            )
        except Exception as error:
            LOGGER.error(
                "Could not verify SMS opt-out status (exception=%s)",
                type(error).__name__,
            )
            await interaction.followup.send(
                "לא ניתן לבדוק אם המספר הוסר. ההודעה לא נשלחה.",
                ephemeral=True,
            )
            return

        if opted_out:
            await interaction.followup.send(
                "המספר ביקש להסיר את עצמו. ההודעה לא נשלחה.",
                ephemeral=True,
            )
            return

        now = time.monotonic()
        async with rate_limit_lock:
            if recipient in pending_numbers:
                await interaction.followup.send(
                    "כבר מתבצעת שליחה למספר הזה. יש להמתין לסיום.",
                    ephemeral=True,
                )
                return

            last_attempt = last_attempt_by_number.get(recipient)
            if last_attempt is not None:
                remaining = RECIPIENT_COOLDOWN_SECONDS - (now - last_attempt)
                if remaining > 0:
                    minutes = max(1, int((remaining + 59) // 60))
                    await interaction.followup.send(
                        f"אפשר לנסות שוב למספר הזה בעוד כ-{minutes} דקות.",
                        ephemeral=True,
                    )
                    return

            pending_numbers.add(recipient)

        try:
            result = await asyncio.to_thread(
                twilio_client.messages.create,
                body=body,
                from_=settings.twilio_number,
                to=recipient,
            )
        except Exception as error:
            # Keep the cooldown after an uncertain provider response to avoid
            # accidentally sending a duplicate if Twilio accepted the message.
            async with rate_limit_lock:
                pending_numbers.discard(recipient)
                last_attempt_by_number[recipient] = time.monotonic()
            LOGGER.error(
                "Twilio SMS request failed (exception=%s, status=%s)",
                type(error).__name__,
                getattr(error, "status", "unknown"),
            )
            await interaction.followup.send(
                "לא ניתן לאשר שההודעה נשלחה. כדי למנוע שליחה כפולה, "
                "בדקו את מצב ההודעה ב-Twilio לפני ניסיון נוסף.",
                ephemeral=True,
            )
            return

        async with rate_limit_lock:
            pending_numbers.discard(recipient)
            last_attempt_by_number[recipient] = time.monotonic()

        await interaction.followup.send(
            f"ההודעה נשלחה. מזהה Twilio: `{result.sid}`",
            ephemeral=True,
        )

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
