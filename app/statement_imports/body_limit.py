import re

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

STATEMENT_UPLOAD_PATH = re.compile(r"/workspaces/\d+/statement-imports")


class _StatementBodyTooLarge(Exception):
    pass


class StatementUploadBodyLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, max_file_bytes: int, multipart_overhead_bytes: int = 64 * 1024
    ) -> None:
        self.app = app
        self.max_body_bytes = max_file_bytes + multipart_overhead_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and STATEMENT_UPLOAD_PATH.fullmatch(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return
        content_length = self._content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await self._reject(scope, receive, send)
            return
        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise _StatementBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _StatementBodyTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        await PlainTextResponse("Statement upload is too large.", status_code=413)(
            scope, receive, send
        )
