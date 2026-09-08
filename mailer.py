import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import NOTIFY_TO, SMTP_APP_PASSWORD, SMTP_EMAIL, SMTP_HOST, SMTP_PORT

# SimplyUtd brand colours (mirror the site UI).
RED = "#DA291C"
BLACK = "#000000"
DARK = "#141414"
WHITE = "#FFFFFF"
MUTED = "#AAAAAA"


def _is_configured() -> bool:
    return bool(SMTP_EMAIL and SMTP_APP_PASSWORD)


def _send(subject: str, to: str, html: str, text: str) -> tuple[bool, str]:
    """Send a multipart (HTML + plain text) email via Gmail SMTP."""
    if not _is_configured():
        return False, "SMTP not configured — skipped email."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"SimplyUtd <{SMTP_EMAIL}>"
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
            server.send_message(msg)
        return True, "Email sent."
    except Exception as exc:  # noqa: BLE001 - surface any SMTP failure to caller
        return False, f"Failed to send email: {exc}"


def _logo() -> str:
    """Branded logo row styled like the site header."""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background-color:{DARK};border-radius:16px 16px 0 0;">'
        f'<tr><td align="center" style="padding:28px 24px 8px 24px;">'
        f'<span style="font-family:Georgia,\'Times New Roman\',serif;font-size:28px;'
        f'font-weight:bold;color:{WHITE};">Simply'
        f'<span style="color:{RED};">Utd</span></span>'
        f'<div style="font-family:Georgia,serif;color:{MUTED};font-size:13px;'
        f'margin-top:4px;font-style:italic;">Your home for Manchester United news</div>'
        f'</td></tr></table>'
    )


def send_welcome_email(email: str) -> tuple[bool, str]:
    """Send the registrant a confirmation that they joined the waitlist."""
    subject = "You're on the list — welcome to SimplyUtd! 🔴"
    text = (
        "Welcome to SimplyUtd!\n\n"
        f"You're on the list ({email}). We're building SimplyUtd — your home for "
        "Manchester United news, updates, stories and everything happening around "
        "the club.\n\n"
        "We'll email you the moment we launch. See you at kick-off.\n\n"
        "— The SimplyUtd team"
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0;padding:0;background-color:{BLACK};">
    <center>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background-color:{BLACK};">
      <tr><td align="center" style="padding:40px 16px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="max-width:560px;width:100%;background-color:{DARK};
                      border-radius:16px;overflow:hidden;">
          {_logo()}
          <tr>
            <td style="padding:28px 32px;">
              <div style="height:3px;width:56px;background-color:{RED};
                          border-radius:2px;margin-bottom:22px;"></div>
              <h1 style="margin:0 0 14px 0;font-family:Georgia,'Times New Roman',serif;
                         font-size:26px;line-height:1.3;color:{WHITE};">
                Something big for United fans is coming&hellip;
              </h1>
              <p style="margin:0 0 18px 0;font-family:Inter,Arial,sans-serif;font-size:15px;
                        line-height:1.7;color:#E6E6E6;">
                Hi there, and welcome to the <strong style="color:{WHITE};">SimplyUtd</strong>
                waitlist. You're officially in the squad&nbsp;
                <span style="color:{WHITE};">{email}</span>.
              </p>
              <p style="margin:0 0 18px 0;font-family:Inter,Arial,sans-serif;font-size:15px;
                        line-height:1.7;color:#E6E6E6;">
                We're building your home for Manchester United news, updates, stories and
                everything happening around the club. The moment we launch, you'll be the
                first to know — straight to this inbox.
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0"
                     style="margin:26px 0 8px 0;">
                <tr>
                  <td style="border-radius:999px;background-color:{WHITE};padding:12px 26px;
                             font-family:Georgia,serif;font-size:14px;font-weight:bold;
                             color:{BLACK};">✓ YOU'RE ON THE LIST</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 32px;background-color:{BLACK};
                       border-radius:0 0 16px 16px;">
              <p style="margin:0;font-family:Inter,Arial,sans-serif;font-size:13px;
                        line-height:1.6;color:{MUTED};text-align:center;">
                We'll be in touch soon. See you at kick-off. ⚽<br/>
                &mdash; The SimplyUtd team
              </p>
            </td>
          </tr>
        </table>
        <p style="margin:22px 0 0 0;font-family:Inter,Arial,sans-serif;font-size:11px;
                  color:#666666;">
          You received this because you joined the SimplyUtd waitlist.
        </p>
      </td></tr>
    </table>
    </center>
    </body>
    </html>
    """
    return _send(subject, email, html, text)


def send_signup_notification(email: str) -> tuple[bool, str]:
    """Email the site owner that a new person joined the waitlist."""
    subject = "New SimplyUtd waitlist signup 🎉"
    text = (
        "A new person joined the SimplyUtd waitlist.\n\n"
        f"Email: {email}\n\n"
        "Keep this address handy — you can import it into your email list or reach out "
        "when SimplyUtd launches."
    )
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="margin:0;padding:0;background-color:{BLACK};">
    <center>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background-color:{BLACK};">
      <tr><td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="max-width:480px;width:100%;background-color:{DARK};
                      border-radius:16px;overflow:hidden;">
          {_logo()}
          <tr>
            <td style="padding:24px 28px;">
              <div style="height:3px;width:56px;background-color:{RED};
                          border-radius:2px;margin-bottom:18px;"></div>
              <h1 style="margin:0 0 12px 0;font-family:Georgia,serif;font-size:22px;
                         color:{WHITE};">New waitlist signup</h1>
              <p style="margin:0 0 6px 0;font-family:Inter,Arial,sans-serif;font-size:14px;
                        color:#E6E6E6;">Email:</p>
              <p style="margin:0 0 18px 0;font-family:Georgia,serif;font-size:18px;
                        font-weight:bold;color:{RED};">{email}</p>
              <p style="margin:0;font-family:Inter,Arial,sans-serif;font-size:13px;
                        color:{MUTED};">Import this address into your email list or reach
                        out when SimplyUtd launches.</p>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
    </center>
    </body>
    </html>
    """
    return _send(subject, NOTIFY_TO, html, text)
