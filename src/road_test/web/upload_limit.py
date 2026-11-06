"""交件的大小上限（ASGI middleware）：在 FastAPI 解析 multipart 之前就擋。

有 Content-Length 的看標頭直接退件；沒有的（分塊上傳）邊收邊數，超過就停，不會整個收下來才檢查。
"""
import json

PATH = "/api/submissions"


class UploadLimit:
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    def _message(self) -> str:
        return (f"模型檔太大了（上限 {self.max_bytes / 1_000_000:g} MB）。"
                "請確認上傳的是訓練程式匯出的 .onnx 檔。")

    async def _refuse(self, send) -> None:
        body = json.dumps({"detail": self._message()}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != PATH:
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            return await self._refuse(send)

        received, exceeded, refused = 0, False, False

        async def counted():
            # 超過上限就假裝上傳結束：FastAPI 解析 multipart 失敗、回錯誤，回應在 replied 換成 413
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def replied(message):
            nonlocal refused
            if not exceeded:
                return await send(message)
            if not refused:
                refused = True
                await self._refuse(send)

        await self.app(scope, counted, replied)
