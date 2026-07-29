import html as html_module
import re
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import os
import os.path
import requests
from .secrets import load_env, get_config


def send_email_alert(subject, body):
    """
    Send an email alert.
    Returns True on success, False on failure.
    """
    return send_email_report(subject, body, attachment_path=None)


def send_email_report(subject, body, attachment_path=None, html_body=None,
                      attachments=None):
    """Send an email report, optionally with attachments.

    When html_body is given the message is multipart/alternative: clients that
    render HTML show that, and anything else falls back to `body`.
    Returns True on success, False on failure.
    """
    config = get_config()
    if not config.get('EMAIL_ENABLED', 'false').lower() == 'true':
        # Email alerts are disabled
        return False
    try:
        secrets = load_env()
        smtp_host = config.get('EMAIL_SMTP_HOST', 'smtp.gmail.com')
        smtp_port = int(config.get('EMAIL_SMTP_PORT', 587))
        username = secrets.get('EMAIL_USERNAME') or os.environ.get('EMAIL_USERNAME')
        password = secrets.get('EMAIL_PASSWORD') or os.environ.get('EMAIL_PASSWORD')
        from_addr = config.get('EMAIL_FROM')
        to_addresses = config.get('EMAIL_TO', '')
        if not to_addresses:
            raise ValueError("EMAIL_TO not configured")
        to_addrs = [addr.strip() for addr in to_addresses.split(',') if addr.strip()]
        if not username or not password:
            raise ValueError("EMAIL_USERNAME or EMAIL_PASSWORD not set in KeePass")

        msg = MIMEMultipart('mixed')
        msg['From'] = from_addr
        msg['To'] = ", ".join(to_addrs)
        msg['Subject'] = subject

        if html_body:
            # The plain part must come first: clients pick the last part they
            # can render, so HTML has to be the later alternative.
            alternative = MIMEMultipart('alternative')
            alternative.attach(MIMEText(body, 'plain', 'utf-8'))
            alternative.attach(MIMEText(html_body, 'html', 'utf-8'))
            msg.attach(alternative)
        else:
            msg.attach(MIMEText(body, 'plain', 'utf-8'))

        wanted = list(attachments or [])
        if attachment_path:
            wanted.append(attachment_path)
        for path in wanted:
            if not path or not os.path.isfile(path):
                continue
            name = os.path.basename(path)
            with open(path, 'rb') as f:
                part = MIMEApplication(f.read(), Name=name)
            part['Content-Disposition'] = f'attachment; filename="{name}"'
            msg.attach(part)

        context = ssl.create_default_context()

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls(context=context)
            server.login(username, password)
            text = msg.as_string()
            server.sendmail(from_addr, to_addrs, text)
        return True
    except Exception as e:
        print(f"Failed to send email alert: {e}", flush=True)
        return False


def telegram_chat_ids(config=None):
    """Every chat the alerts go to.

    TELEGRAM_CHAT_ID takes one id or several separated by commas, so adding
    a second recipient does not mean adding a second setting.
    """
    if config is None:
        config = get_config()
    raw = str(config.get("TELEGRAM_CHAT_ID", ""))
    seen = []
    for piece in raw.replace(";", ",").replace(" ", ",").split(","):
        piece = piece.strip()
        if piece and piece not in seen:
            seen.append(piece)
    return seen


def send_telegram_alert(message):
    """
    Send a Telegram message via the Bot API.
    Returns True on success, False on failure.
    """
    config = get_config()
    if not config.get('TELEGRAM_ENABLED', 'false').lower() == 'true':
        # Telegram alerts are disabled
        return False
    try:
        secrets = load_env()
        bot_token = secrets.get('TELEGRAM_BOT_TOKEN')
        chat_ids = telegram_chat_ids(config)
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set in KeePass")
        if not chat_ids:
            raise ValueError("TELEGRAM_CHAT_ID not configured")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        # 4096 characters is the hard limit; going over is a 400 like any
        # other, so trim here rather than find out from the API.
        if len(message) > 4000:
            message = message[:4000] + "\n…truncated, see the attached log"

        def post(chat_id, text, html):
            payload = {'chat_id': chat_id, 'text': text}
            if html:
                payload['parse_mode'] = 'HTML'
            r = requests.post(url, json=payload, timeout=10)
            if not r.ok:
                raise RuntimeError(f"{r.status_code} {r.text[:200]}")
            return r

        # One recipient failing must not silence the rest, so each is sent
        # separately and the result is "did anyone get it".
        delivered = 0
        for chat_id in chat_ids:
            try:
                post(chat_id, message, html=True)
                delivered += 1
            except Exception as e:
                # A malformed tag in text quoted from a collector should cost
                # the formatting, not the whole notification. Send it again as
                # plain text so the summary still arrives.
                print(f"Telegram: {chat_id} rejected the formatted alert: {e}"
                      " — retrying as plain text", flush=True)
                try:
                    plain = re.sub(r"</?b>", "", message)
                    plain = html_module.unescape(plain)
                    post(chat_id, plain, html=False)
                    delivered += 1
                except Exception as e2:
                    print(f"Telegram: {chat_id} did not receive the alert: "
                          f"{e2}", flush=True)
        return delivered > 0
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}", flush=True)
        return False


def send_telegram_document(document_path, caption=None):
    """
    Send a document to the Telegram chat.
    Returns True on success, False on failure.
    """
    config = get_config()
    if not config.get('TELEGRAM_ENABLED', 'false').lower() == 'true':
        return False
    try:
        secrets = load_env()
        bot_token = secrets.get('TELEGRAM_BOT_TOKEN')
        chat_ids = telegram_chat_ids(config)
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set in KeePass")
        if not chat_ids:
            raise ValueError("TELEGRAM_CHAT_ID not configured")
        if not os.path.isfile(document_path):
            raise FileNotFoundError(f"Document not found: {document_path}")

        url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
        delivered = 0
        for chat_id in chat_ids:
            try:
                # Reopened per recipient: the upload consumes the handle.
                with open(document_path, 'rb') as doc:
                    files = {'document': (os.path.basename(document_path), doc)}
                    data = {'chat_id': chat_id}
                    if caption:
                        data['caption'] = caption
                    response = requests.post(url, data=data, files=files,
                                             timeout=15)
                    response.raise_for_status()
                delivered += 1
            except Exception as e:
                print(f"Telegram: {chat_id} did not receive the document: {e}",
                      flush=True)
        return delivered > 0
    except Exception as e:
        print(f"Failed to send Telegram document: {e}", flush=True)
        return False