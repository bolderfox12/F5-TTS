"""Keep the single-user Studio confined to its loopback browser origin."""

import re

from nicegui import Client, ui
from starlette.responses import PlainTextResponse


LOOPBACK_HOST = re.compile(r"(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?", re.IGNORECASE)
UPLOAD_ROUTE = re.compile(r"/_nicegui/client/([^/]+)/upload/([0-9]+)")
MULTIPART_OVERHEAD = 1024**2


class StudioBrowserBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        headers = scope["headers"]
        hosts = [value.decode("latin-1").lower() for name, value in headers if name == b"host"]
        origins = [value.decode("latin-1") for name, value in headers if name == b"origin"]
        if len(hosts) != 1 or not LOOPBACK_HOST.fullmatch(hosts[0]):
            return await self.reject(scope, receive, send, 400, "Studio requires a loopback host.")
        if origins and origins != [f"http://{hosts[0]}"]:
            return await self.reject(scope, receive, send, 403, "Cross-origin Studio requests are blocked.")

        route = UPLOAD_ROUTE.fullmatch(scope["path"])
        if scope["type"] == "http" and scope["method"] == "POST" and route:
            client = Client.instances.get(route[1])
            element = client.elements.get(int(route[2])) if client else None
            if not isinstance(element, ui.upload):
                return await self.reject(scope, receive, send, 404, "Upload target not found.")
            size = element.props.get("max-file-size") or 100_000_000
            count = element.props.get("max-files") if element.props.get("multiple") else 1
            maximum = min(size * (count or 1), 5_000_000_000) + MULTIPART_OVERHEAD
            lengths = [value for name, value in headers if name == b"content-length"]
            if len(lengths) != 1 or not lengths[0].isdigit():
                return await self.reject(scope, receive, send, 411, "Upload length is required.")
            if len(lengths[0]) > 12 or int(lengths[0]) > maximum:
                return await self.reject(scope, receive, send, 413, "Upload exceeds the size limit.")

        return await self.app(scope, receive, send)

    @staticmethod
    async def reject(scope, receive, send, status, message):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await PlainTextResponse(message, status_code=status)(scope, receive, send)
