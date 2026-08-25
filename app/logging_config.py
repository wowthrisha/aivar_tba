"""JSON logging for observability (T-16).

Every log line is a single JSON object on stdout - Railway (and any
other 12-factor host) captures stdout directly, no log-file wiring
needed. `request_id` is carried via a contextvar so any code running
within a request's async task tree can log with it attached, without
threading it through every function signature.
"""

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# D-36: same pattern set as scripts/scan-secrets.sh and scripts/pre-commit
# (minus the generic 40-hex-char check, which false-positives on git SHAs
# this project logs constantly - see scan-secrets.sh's comment for why).
# Defense in depth: the app should never deliberately log a secret, but
# this catches one that slips into a message or exception string (e.g.
# an exception whose str() happens to include a connection string).
_SECRET_PATTERN = re.compile(
    r"npg_[A-Za-z0-9]{10,}"
    r"|sk-proj-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|postgres(?:ql)?://[^\s]*:[^\s@]*@[^\s]*"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
    r"|(?:SECRET|TOKEN|PASSWORD|API_KEY)[A-Za-z_]*\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{16,}",
    re.IGNORECASE | re.DOTALL,
)


def _redact(text: str) -> str:
    return _SECRET_PATTERN.sub("[REDACTED]", text)


class RedactSecretsFilter(logging.Filter):
    """Masks secret-shaped substrings in a record's message before it
    reaches any handler/formatter. Never drops a record - always
    returns True - only sanitizes it."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            # Redacted separately: this text comes from the traceback
            # object, not record.msg, so RedactSecretsFilter (which only
            # touches record.msg/args) never sees it.
            payload["exc_info"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging() -> None:
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return  # already configured - safe to call more than once
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactSecretsFilter())
    root.handlers = [handler]
    root.setLevel(logging.INFO)
