import hashlib
import os

import psycopg


def recipient_phone_hash(phone_number: str) -> str:
    return hashlib.sha256(phone_number.encode("utf-8")).hexdigest()


def is_recipient_opted_out(phone_number: str) -> bool:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    with psycopg.connect(database_url, connect_timeout=5) as connection:
        row = connection.execute(
            """
            SELECT status
            FROM sms_recipient_preferences
            WHERE phone_hash = %s
            """,
            (recipient_phone_hash(phone_number),),
        ).fetchone()

    return row is not None and row[0] == "opted_out"
