"""
Sends the daily digest by email via SMTP (compatible Mailjet, Resend SMTP, etc.).
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


def send_digest(
    html_body: str,
    plain_body: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    sender_address: str,
    sender_name: str,
    recipient: str,
    date_label: str,
) -> None:
    """Sends the digest. `date_label` is formatted upstream so the subject and
    the mail header can never disagree, and so no date wording lives here."""
    subject = f"Des nouvelles des étoiles — {date_label}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender_address}>"
    msg["To"] = recipient

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(sender_address, [recipient], msg.as_string())

    log.info("Digest sent to %s", recipient)
