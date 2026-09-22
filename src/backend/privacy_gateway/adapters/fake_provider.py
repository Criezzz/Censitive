"""A deterministic offline responder consuming only serialized provider-bound text."""

import asyncio
import json

from privacy_gateway.domain.redaction import TOKEN


class FakeProvider:
    provider_id = "local-demo"
    model = "template-v1"
    serializer_version = "1"
    timeout = 10.0

    async def complete(self, body: bytes) -> str:
        await asyncio.sleep(0.65)
        payload = json.loads(body)
        text: str = payload["messages"][-1]["content"]
        tokens = TOKEN.findall(text)
        if "email" in text.lower() or "thư" in text.lower():
            greeting = tokens[0] if tokens else "bạn"
            return (
                f"Kính gửi {greeting},\n\n"
                "Tôi gửi bạn bản nháp để cùng trao đổi về nội dung sau:\n"
                f"{text}\n\n"
                "Mong nhận được phản hồi của bạn để thống nhất bước tiếp theo.\n\nTrân trọng."
            )
        return (
            f"Tôi đã nhận yêu cầu:\n\n{text}\n\n"
            "Bạn có thể bổ sung mục tiêu hoặc chỉnh lại bản nháp trước khi sử dụng."
        )
