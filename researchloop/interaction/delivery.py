"""Finite buffered evidence handoff on the scoped Gunicorn transport."""

import time


class BoundedDelivery:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "").rstrip("/")
        from researchloop.evidence_delivery import transport

        if transport.protected_path(
            path,
            environ.get("QUERY_STRING", ""),
            environ.get("HTTP_EVIDENCE_SELECTION", ""),
        ):
            if transport.request_socket() is not None:
                return self.application(environ, start_response)
            payload = b'{"type":"about:blank","code":"DELIVERY_UNAVAILABLE","title":"Bounded evidence delivery unavailable","status":503,"requestId":"local-delivery"}'
            start_response(
                "503 Service Unavailable",
                [
                    ("Content-Type", "application/problem+json"),
                    ("Content-Length", str(len(payload))),
                    ("Cache-Control", "no-store"),
                    ("Referrer-Policy", "no-referrer"),
                ],
            )
            return [payload]
        evidence = (
            path.endswith("/minimal-results")
            or path.startswith("/api/v1/participant/")
            or (path.startswith("/api/v1/recruit/") and path.endswith("/sessions"))
        )
        if not evidence:
            return self.application(environ, start_response)
        socket = environ.get("gunicorn.socket")
        if socket is None:
            payload = b'{"type":"about:blank","code":"DELIVERY_UNAVAILABLE","title":"Bounded evidence delivery unavailable","status":503,"requestId":"local-delivery"}'
            start_response(
                "503 Service Unavailable",
                [
                    ("Content-Type", "application/problem+json"),
                    ("Content-Length", str(len(payload))),
                    ("Cache-Control", "no-store"),
                ],
            )
            return [payload]
        deadline = time.monotonic() + 5
        socket.settimeout(5)
        body = self.application(environ, start_response)

        def bounded_body():
            try:
                for chunk in body:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Evidence handoff expired")
                    socket.settimeout(remaining)
                    yield chunk
            finally:
                if hasattr(body, "close"):
                    body.close()

        return bounded_body()
