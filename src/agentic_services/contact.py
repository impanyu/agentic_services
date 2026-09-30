from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Settings


def send_email(
    settings: Settings,
    *,
    recipient: str,
    subject: str,
    body: str,
    reply_to: str | None = None,
) -> None:
    username = settings.contact_smtp_username
    password = settings.contact_smtp_app_password
    if not username or not password:
        raise RuntimeError("Contact email delivery is not configured")

    message = EmailMessage()
    message["From"] = f"Dream Workshop <{username}>"
    message["To"] = recipient
    if reply_to:
        message["Reply-To"] = reply_to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP_SSL(
        settings.contact_smtp_host, settings.contact_smtp_port, timeout=15
    ) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)


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
    service_line = service_id or "General inquiry"
    send_email(
        settings,
        recipient=settings.contact_recipient_email,
        subject=f"[Dream Workshop] {subject}",
        reply_to=sender_email,
        body="\n".join(
            (f"Message ID: {message_id}", f"From: {sender_name} <{sender_email}>",
             f"Service: {service_line}", "", message_text)
        ),
    )
