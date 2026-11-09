import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

import discord
from discord.ext import commands

from sms_accounting import adjust_user_credits, get_credit_balance

LOGGER = logging.getLogger("discord_sms_bot.ui")
SmsSubmit = Callable[[discord.Interaction, str, str, bool], Awaitable[None]]


def _is_admin(interaction: discord.Interaction) -> bool:
    return (
        isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.administrator
    )


class SmsComposeModal(discord.ui.Modal):
    def __init__(self, submit_sms: SmsSubmit) -> None:
        super().__init__(title="Compose an SMS")
        self.submit_sms = submit_sms
        self.phone_input = discord.ui.TextInput(
            label="Recipient phone number",
            placeholder="+972501234567",
            min_length=9,
            max_length=20,
            required=True,
        )
        self.message_input = discord.ui.TextInput(
            label="Message",
            placeholder="Write one message to one recipient",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=500,
            required=True,
        )
        self.consent_input = discord.ui.TextInput(
            label="Consent confirmation",
            placeholder="Type YES only if the recipient opted in",
            min_length=3,
            max_length=3,
            required=True,
        )
        self.add_item(self.phone_input)
        self.add_item(self.message_input)
        self.add_item(self.consent_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.consent_input.value.strip().upper() != "YES":
            await interaction.response.send_message(
                "Type YES only after confirming the recipient explicitly opted in.",
                ephemeral=True,
            )
            return

        await self.submit_sms(
            interaction,
            self.phone_input.value,
            self.message_input.value,
            True,
        )


class CreditAdjustmentModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, action: str) -> None:
        title = "Grant credits" if action == "grant" else "Set credit balance"
        super().__init__(title=title)
        self.bot = bot
        self.action = action
        self.user_id_input = discord.ui.TextInput(
            label="Discord user ID",
            placeholder="Paste the user's numeric ID",
            min_length=17,
            max_length=20,
            required=True,
        )
        self.amount_input = discord.ui.TextInput(
            label="Credits" if action == "grant" else "New balance",
            placeholder="1 to 100000",
            min_length=1,
            max_length=6,
            required=True,
        )
        self.add_item(self.user_id_input)
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot owner can change credit balances.",
                ephemeral=True,
            )
            return

        target_user_id = self.user_id_input.value.strip()
        if not re.fullmatch(r"[0-9]{17,20}", target_user_id):
            await interaction.response.send_message(
                "Enter a valid numeric Discord user ID.",
                ephemeral=True,
            )
            return

        try:
            amount = int(self.amount_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "Enter a whole-number credit amount.",
                ephemeral=True,
            )
            return

        minimum = 1 if self.action == "grant" else 0
        if not minimum <= amount <= 100_000:
            await interaction.response.send_message(
                "The amount must be between 1 and 100000 for a grant, "
                "or 0 and 100000 for a balance reset.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            balance = await asyncio.to_thread(
                adjust_user_credits,
                str(interaction.user.id),
                target_user_id,
                amount,
                self.action,
            )
        except Exception as error:
            LOGGER.error(
                "Credit adjustment failed (exception=%s)",
                type(error).__name__,
            )
            await interaction.followup.send(
                "Could not update credits. No balance change was confirmed.",
                ephemeral=True,
            )
            return

        action_message = "granted" if self.action == "grant" else "set"
        await interaction.followup.send(
            f"Credits {action_message} for user `{target_user_id}`. "
            f"New balance: **{balance}**.",
            ephemeral=True,
        )


class OwnerCreditView(discord.ui.View):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=180)
        self.bot = bot

    @discord.ui.button(
        label="Grant credits",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def grant_credits(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot owner can change credit balances.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            CreditAdjustmentModal(self.bot, "grant")
        )

    @discord.ui.button(
        label="Set balance",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def set_credits(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only the bot owner can change credit balances.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            CreditAdjustmentModal(self.bot, "set")
        )


class SmsPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, submit_sms: SmsSubmit) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.submit_sms = submit_sms

    @discord.ui.button(
        label="Compose SMS",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def compose_sms(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message(
                "Only server administrators can send SMS messages.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            SmsComposeModal(self.submit_sms)
        )

    @discord.ui.button(
        label="My credits",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def my_credits(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
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
                "Credit balance is temporarily unavailable.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Your SMS credit balance is **{balance}**.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Owner credit controls",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def owner_controls(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "These credit controls are available only to the bot owner.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Owner credit controls",
            description=(
                "Grant credits or set a user's balance. Every change is "
                "recorded in the credit ledger."
            ),
            color=discord.Color.from_rgb(73, 68, 220),
        )
        await interaction.response.send_message(
            embed=embed,
            view=OwnerCreditView(self.bot),
            ephemeral=True,
        )
