import email
import imaplib
import logging
import threading
from contextlib import closing
from email.header import decode_header, make_header
from typing import Optional


class EmailEventIngestor:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        assistant,
        folder: str = "INBOX",
        port: Optional[int] = None,
        use_ssl: bool = True,
        poll_interval: int = 60,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.folder = folder
        self.port = port
        self.use_ssl = use_ssl
        self.poll_interval = poll_interval
        self.assistant = assistant
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.logger = logging.getLogger(self.__class__.__name__)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.logger.info("Email ingestor started for %s", self.username)

    def stop(self):
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join()

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._check_inbox()
            except Exception:
                self.logger.exception("Email polling failed")
            self._stop_event.wait(self.poll_interval)

    def _check_inbox(self):
        with closing(self._connect()) as mailbox:
            mailbox.select(self.folder)
            status, data = mailbox.search(None, "UNSEEN")
            if status != "OK":
                self.logger.warning("Failed to search inbox: %s", status)
                return

            message_ids = data[0].split()
            for message_id in message_ids:
                status, msg_data = mailbox.fetch(message_id, "(RFC822)")
                if status != "OK":
                    continue
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                subject = self._decode_header(msg.get("Subject", ""))
                body = self._extract_body(msg)
                context = {
                    "source": "email",
                    "email_subject": subject,
                    "from": self._decode_header(msg.get("From", "")),
                    "to": self._decode_header(msg.get("To", "")),
                }
                result = self.assistant.process_email_payload(subject, body, context)
                if result.success and result.events:
                    mailbox.store(message_id, "+FLAGS", "\\Seen")
                    self.logger.info("Event created from email '%s' (%d events)", subject, len(result.events))
                else:
                    self.logger.info("Email '%s' did not contain an event.", subject)

    def _connect(self):
        if self.use_ssl:
            mailbox = imaplib.IMAP4_SSL(self.host, self.port or 993)
        else:
            mailbox = imaplib.IMAP4(self.host, self.port or 143)
        mailbox.login(self.username, self.password)
        return mailbox

    def _decode_header(self, value: str) -> str:
        if not value:
            return ""
        decoded = make_header(decode_header(value))
        return str(decoded)

    def _extract_body(self, msg) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = part.get("Content-Disposition", "")
                if content_type == "text/plain" and "attachment" not in disposition:
                    charset = part.get_content_charset() or "utf-8"
                    return part.get_payload(decode=True).decode(charset, errors="replace")
        else:
            charset = msg.get_content_charset() or "utf-8"
            return msg.get_payload(decode=True).decode(charset, errors="replace")
        return ""
