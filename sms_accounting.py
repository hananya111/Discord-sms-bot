import os
import re
from dataclasses import dataclass
from typing import Literal

import psycopg

from sms_opt_out import recipient_phone_hash

MAX_USER_SMS_PER_HOUR = 3
MAX_USER_SMS_PER_DAY = 10
MAX_RECIPIENT_SMS_PER_HOUR = 1
MAX_CREDIT_BALANCE = 100_000
E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")

CreditAction = Literal["grant", "set"]
AttemptStatus = Literal["submitted", "uncertain"]


@dataclass(frozen=True)
class SmsReservation:
    allowed: bool
    reason: str
    attempt_id: int | None = None
    remaining_credits: int = 0


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return database_url


def get_credit_balance(discord_user_id: str) -> int:
    with psycopg.connect(_database_url(), connect_timeout=5) as connection:
        row = connection.execute(
            """
            SELECT balance
            FROM sms_user_credits
            WHERE discord_user_id = %s
            """,
            (discord_user_id,),
        ).fetchone()
    return int(row[0]) if row else 0


def adjust_user_credits(
    actor_user_id: str,
    target_user_id: str,
    amount: int,
    action: CreditAction,
) -> int:
    if action not in ("grant", "set"):
        raise ValueError("Unsupported credit action.")
    if amount < 0 or amount > MAX_CREDIT_BALANCE:
        raise ValueError(f"Credits must be between 0 and {MAX_CREDIT_BALANCE}.")
    if action == "grant" and amount == 0:
        raise ValueError("Grant at least one credit.")

    with psycopg.connect(_database_url(), connect_timeout=5) as connection:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO sms_user_credits (discord_user_id, balance)
                VALUES (%s, 0)
                ON CONFLICT (discord_user_id) DO NOTHING
                """,
                (target_user_id,),
            )
            row = connection.execute(
                """
                SELECT balance
                FROM sms_user_credits
                WHERE discord_user_id = %s
                FOR UPDATE
                """,
                (target_user_id,),
            ).fetchone()
            current_balance = int(row[0]) if row else 0
            new_balance = (
                current_balance + amount if action == "grant" else amount
            )
            if new_balance > MAX_CREDIT_BALANCE:
                raise ValueError(
                    f"Balance cannot exceed {MAX_CREDIT_BALANCE} credits."
                )

            connection.execute(
                """
                UPDATE sms_user_credits
                SET balance = %s, updated_at = NOW()
                WHERE discord_user_id = %s
                """,
                (new_balance, target_user_id),
            )
            connection.execute(
                """
                INSERT INTO sms_credit_ledger (
                    discord_user_id, actor_user_id, action,
                    amount_delta, balance_after
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    target_user_id,
                    actor_user_id,
                    action,
                    new_balance - current_balance,
                    new_balance,
                ),
            )
    return new_balance


def reserve_sms_attempt(
    discord_user_id: str,
    recipient: str,
    consent_confirmed: bool,
) -> SmsReservation:
    if not consent_confirmed:
        return SmsReservation(False, "consent_required")
    if not E164_PATTERN.fullmatch(recipient):
        return SmsReservation(False, "invalid_number")

    recipient_hash = recipient_phone_hash(recipient)
    with psycopg.connect(_database_url(), connect_timeout=5) as connection:
        with connection.transaction():
            # Serialize attempts to the same recipient, even when they come
            # from different Discord users.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (recipient_hash,),
            )

            preference = connection.execute(
                """
                SELECT status
                FROM sms_recipient_preferences
                WHERE phone_hash = %s
                """,
                (recipient_hash,),
            ).fetchone()
            if preference and preference[0] == "opted_out":
                return SmsReservation(False, "opted_out")

            credit_row = connection.execute(
                """
                SELECT balance
                FROM sms_user_credits
                WHERE discord_user_id = %s
                FOR UPDATE
                """,
                (discord_user_id,),
            ).fetchone()
            if not credit_row or int(credit_row[0]) < 1:
                return SmsReservation(False, "no_credits")

            user_hour_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM sms_send_attempts
                WHERE discord_user_id = %s
                  AND created_at > NOW() - INTERVAL '1 hour'
                """,
                (discord_user_id,),
            ).fetchone()[0]
            if int(user_hour_count) >= MAX_USER_SMS_PER_HOUR:
                return SmsReservation(False, "user_hourly_limit")

            user_day_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM sms_send_attempts
                WHERE discord_user_id = %s
                  AND created_at > NOW() - INTERVAL '24 hours'
                """,
                (discord_user_id,),
            ).fetchone()[0]
            if int(user_day_count) >= MAX_USER_SMS_PER_DAY:
                return SmsReservation(False, "user_daily_limit")

            recipient_hour_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM sms_send_attempts
                WHERE recipient_hash = %s
                  AND created_at > NOW() - INTERVAL '1 hour'
                """,
                (recipient_hash,),
            ).fetchone()[0]
            if int(recipient_hour_count) >= MAX_RECIPIENT_SMS_PER_HOUR:
                return SmsReservation(False, "recipient_hourly_limit")

            new_balance = int(credit_row[0]) - 1
            connection.execute(
                """
                UPDATE sms_user_credits
                SET balance = %s, updated_at = NOW()
                WHERE discord_user_id = %s
                """,
                (new_balance, discord_user_id),
            )
            attempt = connection.execute(
                """
                INSERT INTO sms_send_attempts (
                    discord_user_id, recipient_hash, consent_confirmed_at
                )
                VALUES (%s, %s, NOW())
                RETURNING id
                """,
                (discord_user_id, recipient_hash),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO sms_credit_ledger (
                    discord_user_id, actor_user_id, action,
                    amount_delta, balance_after
                )
                VALUES (%s, %s, 'sms_send', -1, %s)
                """,
                (discord_user_id, discord_user_id, new_balance),
            )

    return SmsReservation(
        allowed=True,
        reason="reserved",
        attempt_id=int(attempt[0]),
        remaining_credits=new_balance,
    )


def finish_sms_attempt(
    attempt_id: int,
    status: AttemptStatus,
    twilio_sid: str | None = None,
) -> None:
    if status not in ("submitted", "uncertain"):
        raise ValueError("Unsupported SMS attempt status.")

    with psycopg.connect(_database_url(), connect_timeout=5) as connection:
        connection.execute(
            """
            UPDATE sms_send_attempts
            SET status = %s, twilio_sid = %s
            WHERE id = %s
            """,
            (status, twilio_sid, attempt_id),
        )
