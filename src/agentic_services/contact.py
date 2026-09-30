from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Settings


def send_contact_email(
    settings: Settings,
    *,
    message_id: str,
    sender_name: str,
    sender_email: str,
    subject: str,
    message_text: str,
    service_id: str | None,
) -> None:
    username = settings.contact_smtp_username
    password = settings.contact_smtp_app_password
    if not username or not password:
        raise RuntimeError("Contact email delivery is not configured")

    message = EmailMessage()
    message["From"] = f"Dream Workshop website <{username}>"
    message["To"] = settings.contact_recipient_email
    message["Reply-To"] = sender_email
    message["Subject"] = f"[Dream Workshop] {subject}"
    service_line = service_id or "General inquiry"
    message.set_content(
        "\n".join(
            (
                f"Message ID: {message_id}",
                f"From: {sender_name} <{sender_email}>",
                f"Service: {service_line}",
                "",
                message_text,
            )
        )
    )

    with smtplib.SMTP_SSL(
        settings.contact_smtp_host, settings.contact_smtp_port, timeout=15
    ) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)
