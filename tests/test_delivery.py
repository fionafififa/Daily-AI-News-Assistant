import socket
from email import policy
from email.parser import BytesParser

from aiosmtpd.controller import Controller

from app.delivery import render_email, send_email


def test_real_smtp_protocol_and_safe_email_render(settings):
    received = []

    class Sink:
        async def handle_DATA(self, server, session, envelope):
            received.append(envelope)
            return "250 Message accepted for delivery"

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    controller = Controller(Sink(), hostname="127.0.0.1", port=port)
    controller.start()
    try:
        settings.smtp_host, settings.smtp_port = "127.0.0.1", port
        settings.smtp_security, settings.smtp_from = "plain", "daily@example.com"
        brief = {
            "title": "测试简报",
            "overview": "<script>alert(1)</script>",
            "demo": True,
            "warnings": [],
            "items": [
                {
                    "title": "标题",
                    "summary": "事实摘要",
                    "why_it_matters": "推荐理由",
                    "source": "测试来源",
                    "published_at": "2026-10-18T00:00:00+00:00",
                    "url": "https://example.com/news",
                }
            ],
        }
        send_email(settings, {"email": "reader@example.com"}, {"id": "smtp-test", "brief": brief})
        assert len(received) == 1
        assert received[0].rcpt_tos == ["reader@example.com"]
        message = BytesParser(policy=policy.default).parsebytes(received[0].content)
        assert message["Subject"] == "测试简报"
        html = message.get_body(preferencelist=("html",)).get_content()
        assert "<script>" not in html and "&lt;script&gt;" in html
        assert "https://example.com/news" in html
        assert "演示数据" in render_email(brief)[0]
    finally:
        controller.stop()
