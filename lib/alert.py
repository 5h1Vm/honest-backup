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


def send_email_report(subject, body, attachment_path=None):
    """
    Send an email report, optionally with an attachment.
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

        msg = MIMEMultipart()
        msg['From'] = from_addr
        msg['To'] = ", ".join(to_addrs)
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain'))

        if attachment_path and os.path.isfile(attachment_path):
            with open(attachment_path, 'rb') as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            # After the file is closed
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
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
        chat_id = config.get('TELEGRAM_CHAT_ID')
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set in KeePass")
        if not chat_id:
            raise ValueError("TELEGRAM_CHAT_ID not configured")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
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
        chat_id = config.get('TELEGRAM_CHAT_ID')
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set in KeePass")
        if not chat_id:
            raise ValueError("TELEGRAM_CHAT_ID not configured")
        if not os.path.isfile(document_path):
            raise FileNotFoundError(f"Document not found: {document_path}")

        url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
        with open(document_path, 'rb') as doc:
            files = {'document': (os.path.basename(document_path), doc)}
            data = {'chat_id': chat_id}
            if caption:
                data['caption'] = caption
            response = requests.post(url, data=data, files=files, timeout=15)
            response.raise_for_status()
        return True
    except Exception as e:
        print(f"Failed to send Telegram document: {e}", flush=True)
        return False