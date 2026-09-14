"""Transactional email via the Resend HTTPS API.

SMTP ports are blocked on many hosts (e.g. Render free tier), so email is sent
over HTTPS. Resend sits behind Cloudflare bot protection which rejects
``Python-urllib``/``httpx`` default User-Agents with error 1010, so a
browser-like UA is required. When Resend is not configured, sending is skipped
and a ``(False, reason)`` tuple is returned instead of raising.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger("simplyutd.mailer")

RED = "#DA291C"
BLACK = "#000000"
DARK = "#141414"
WHITE = "#FFFFFF"
MUTED = "#AAAAAA"

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def is_configured() -> bool:
    return settings.email_configured


def _logo() -> str:
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background-color:{DARK};border-radius:16px 16px 0 0;">'
        f'<tr><td align="center" style="padding:28px 24px 8px 24px;">'
        f'<span style="font-family:Georgia,\'Times New Roman\',serif;font-size:28px;'
        f'font-weight:bold;color:{WHITE};">Simply'
        f'<span style="color:{RED};">Utd</span></span>'
        f'<div style="font-family:Georgia,serif;color:{MUTED};font-size:13px;'
        f'margin-top:4px;font-style:italic;">Your home for Manchester United news</div>'
        f"</td></tr></table>"
    )


def _wrap(inner: str, *, footer: str) -> str:
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background-color:{BLACK};">
<center>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{BLACK};">
  <tr><td align="center" style="padding:40px 16px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="max-width:560px;width:100%;background-color:{DARK};border-radius:16px;overflow:hidden;">
      {_logo()}
      <tr><td style="padding:28px 32px;">{inner}</td></tr>
      <tr><td style="padding:24px 32px;background-color:{BLACK};border-radius:0 0 16px 16px;">
        <p style="margin:0;font-family:Inter,Arial,sans-serif;font-size:13px;line-height:1.6;
                  color:{MUTED};text-align:center;">{footer}</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</center>
</body></html>"""


def _send(subject: str, to: str, html: str, text: str) -> tuple[bool, str]:
    """Send a branded (HTML + plain text) email via Resend."""
    if not to:
        return False, "No recipient address."
    if not is_configured():
        missing = [
            name
            for name, value in (
                ("RESEND_API_KEY", settings.resend_api_key),
                ("RESEND_FROM", settings.resend_from),
            )
            if not value
        ]
        logger.warning("Resend not configured (%s) — skipped '%s'", ", ".join(missing), subject)
        return False, "Resend not configured — skipped email."

    payload = {
        "from": settings.resend_from,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    try:
        response = httpx.post(
            settings.resend_api_url,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "User-Agent": _BROWSER_UA,
            },
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 - surface any network failure
        logger.exception("Resend request failed for '%s' -> %s", subject, to)
        return False, f"Failed to send email: {exc}"

    if response.status_code >= 400:
        detail = response.text
        try:
            body = response.json()
            detail = body.get("message") or detail
            if body.get("name"):
                detail = f"{detail} [{body['name']}]"
        except ValueError:
            pass
        logger.warning("Resend %s for '%s': %s", response.status_code, subject, detail)
        return False, f"Resend API error {response.status_code}: {detail}"

    logger.info("Email sent: '%s' -> %s", subject, to)
    return True, "Email sent."


def send_welcome_email(email: str) -> tuple[bool, str]:
    """Confirm to a newsletter subscriber that they joined."""
    subject = "You're on the list — welcome to SimplyUtd! 🔴"
    text = (
        "Welcome to SimplyUtd!\n\n"
        f"You're on the list ({email}). We're building SimplyUtd — your home for "
        "Manchester United news, updates, stories and everything happening around the club.\n\n"
        "We'll email you when it matters. See you at kick-off.\n\n"
        "— The SimplyUtd team"
    )
    inner = f"""
      <div style="height:3px;width:56px;background-color:{RED};border-radius:2px;margin-bottom:22px;"></div>
      <h1 style="margin:0 0 14px 0;font-family:Georgia,'Times New Roman',serif;font-size:26px;
                 line-height:1.3;color:{WHITE};">Something big for United fans is coming&hellip;</h1>
      <p style="margin:0 0 18px 0;font-family:Inter,Arial,sans-serif;font-size:15px;
                line-height:1.7;color:#E6E6E6;">
        Hi there, and welcome to the <strong style="color:{WHITE};">SimplyUtd</strong> list.
        You're officially in the squad&nbsp;<span style="color:{WHITE};">{email}</span>.
      </p>
      <p style="margin:0 0 18px 0;font-family:Inter,Arial,sans-serif;font-size:15px;
                line-height:1.7;color:#E6E6E6;">
        We're building your home for Manchester United news, updates, stories and everything
        happening around the club. The moment we launch, you'll be the first to know.
      </p>
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:26px 0 8px 0;"><tr>
        <td style="border-radius:999px;background-color:{WHITE};padding:12px 26px;
                   font-family:Georgia,serif;font-size:14px;font-weight:bold;color:{BLACK};">
          ✓ YOU'RE ON THE LIST</td></tr></table>"""
    return _send(subject, email, _wrap(inner, footer="We'll be in touch soon. See you at kick-off. ⚽<br/>&mdash; The SimplyUtd team"), text)


def send_signup_notification(email: str) -> tuple[bool, str]:
    """Notify the site owner of a new subscriber."""
    subject = "New SimplyUtd signup 🎉"
    text = f"A new person joined the SimplyUtd list.\n\nEmail: {email}\n"
    inner = f"""
      <div style="height:3px;width:56px;background-color:{RED};border-radius:2px;margin-bottom:18px;"></div>
      <h1 style="margin:0 0 12px 0;font-family:Georgia,serif;font-size:22px;color:{WHITE};">
        New signup</h1>
      <p style="margin:0 0 6px 0;font-family:Inter,Arial,sans-serif;font-size:14px;color:#E6E6E6;">Email:</p>
      <p style="margin:0 0 18px 0;font-family:Georgia,serif;font-size:18px;font-weight:bold;
                color:{RED};">{email}</p>"""
    return _send(subject, settings.notify_to, _wrap(inner, footer="SimplyUtd admin notification"), text)


def send_contact_notification(name: str, email: str, ctype: str, subject: str, message: str) -> tuple[bool, str]:
    """Notify the owner of a new contact-form submission."""
    mail_subject = f"New SimplyUtd message: {subject}"
    text = (
        f"New contact message via SimplyUtd.\n\nName: {name}\nEmail: {email}\n"
        f"Type: {ctype}\nSubject: {subject}\n\n{message}\n"
    )
    inner = f"""
      <div style="height:3px;width:56px;background-color:{RED};border-radius:2px;margin-bottom:18px;"></div>
      <h1 style="margin:0 0 12px 0;font-family:Georgia,serif;font-size:22px;color:{WHITE};">
        New contact message</h1>
      <p style="margin:0 0 4px 0;font-family:Inter,Arial,sans-serif;font-size:14px;color:#E6E6E6;">
        <strong style="color:{WHITE};">{name}</strong> &lt;{email}&gt;</p>
      <p style="margin:0 0 14px 0;font-family:Inter,Arial,sans-serif;font-size:13px;color:{MUTED};">
        {ctype} &middot; {subject}</p>
      <p style="margin:0;font-family:Inter,Arial,sans-serif;font-size:15px;line-height:1.7;
                color:#E6E6E6;white-space:pre-wrap;">{message}</p>"""
    return _send(mail_subject, settings.notify_to, _wrap(inner, footer="SimplyUtd admin notification"), text)


def send_contact_autoreply(name: str, email: str) -> tuple[bool, str]:
    """Acknowledge a contact-form submission to the sender."""
    subject = "We got your message — SimplyUtd"
    text = (
        f"Hi {name},\n\nThanks for reaching out to SimplyUtd. A member of the team will "
        "get back to you within 2 working days.\n\n— The SimplyUtd team"
    )
    inner = f"""
      <div style="height:3px;width:56px;background-color:{RED};border-radius:2px;margin-bottom:22px;"></div>
      <h1 style="margin:0 0 14px 0;font-family:Georgia,serif;font-size:24px;color:{WHITE};">
        Thanks for getting in touch</h1>
      <p style="margin:0;font-family:Inter,Arial,sans-serif;font-size:15px;line-height:1.7;color:#E6E6E6;">
        Hi {name}, we've received your message and a member of the SimplyUtd team will reply
        within 2 working days.</p>"""
    return _send(subject, email, _wrap(inner, footer="&mdash; The SimplyUtd team"), text)


def send_test_email(to: str) -> tuple[bool, str]:
    subject = "SimplyUtd API — email test ✅"
    text = "This is a test email from your SimplyUtd backend. If you can read this, delivery works."
    inner = f"""
      <h1 style="margin:0 0 12px 0;font-family:Georgia,serif;font-size:22px;color:{WHITE};">Email test</h1>
      <p style="margin:0;font-family:Inter,Arial,sans-serif;font-size:14px;line-height:1.7;color:#E6E6E6;">
        If you can read this, email delivery is working end to end.&nbsp;<span style="color:{WHITE};">⚽</span></p>"""
    return _send(subject, to, _wrap(inner, footer="&mdash; The SimplyUtd team"), text)
