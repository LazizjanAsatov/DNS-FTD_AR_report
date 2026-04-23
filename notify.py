"""Telegram sender. Uses urllib — no extra dependency."""
import json
import logging
import mimetypes
import os
import uuid
from urllib import request

log = logging.getLogger(__name__)


def _post(url, fields=None, files=None, timeout=60):
    """files values are either a path (str/bytes) or a (filename, bytes) tuple."""
    fields = fields or {}
    files = files or {}
    boundary = uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode()
    for k, value in files.items():
        if isinstance(value, tuple):
            filename, data = value
        else:
            filename = os.path.basename(value)
            with open(value, "rb") as f:
                data = f.read()
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"; filename="{filename}"\r\n'.encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = request.Request(url, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def send_message(token, chat_id, text, thread_id=None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    fields = {"chat_id": str(chat_id), "text": text}
    if thread_id:
        fields["message_thread_id"] = str(thread_id)
    try:
        res = _post(url, fields=fields)
        if not res.get("ok"):
            log.error("Telegram sendMessage failed: %s", res)
        return res.get("ok", False)
    except Exception as e:
        log.exception("Telegram sendMessage error: %s", e)
        return False


def send_document(token, chat_id, document, caption=None, thread_id=None, filename=None):
    """document: file path (str) OR bytes. If bytes, filename is required."""
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    fields = {"chat_id": str(chat_id)}
    if thread_id:
        fields["message_thread_id"] = str(thread_id)
    if caption:
        fields["caption"] = caption
    if isinstance(document, (bytes, bytearray)):
        if not filename:
            raise ValueError("filename required when sending bytes")
        files = {"document": (filename, bytes(document))}
    else:
        files = {"document": document}
    try:
        res = _post(url, fields=fields, files=files, timeout=300)
        if not res.get("ok"):
            log.error("Telegram sendDocument failed: %s", res)
        return res.get("ok", False)
    except Exception as e:
        log.exception("Telegram sendDocument error: %s", e)
        return False
