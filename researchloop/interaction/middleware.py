import logging
import re


class RedactCapabilities(logging.Filter):
    def filter(self, record):
        text = record.getMessage()
        record.msg = re.sub(
            r"(/(?:api/v1/)?(?:participant|recruit|downloads)/)[^/ ?\"]+",
            r"\1[redacted]",
            text,
        )
        record.args = ()
        return True


class SecurityHeaders:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response
