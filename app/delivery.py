import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape

from .article_time import date_labels


def render_email(brief):
    """Render persisted structured data; never ask the model to regenerate email content."""
    intro = "【演示数据，非实时新闻】\n" if brief["demo"] else ""
    text = intro + brief["title"] + "\n\n" + brief["overview"] + "\n"
    blocks = [f"<h1>{escape(brief['title'])}</h1>", f"<p>{escape(intro + brief['overview'])}</p>"]
    for i, item in enumerate(brief["items"], 1):
        text += (
            f"\n{i}. {item['title']}\n{item['summary']}\n与你的关系：{item['why_it_matters']}\n"
            f"来源：{item['source']} | {date_labels(item)}\n{item['url']}\n"
        )
        blocks.append(
            f"<h2>{i}. {escape(item['title'])}</h2><p>{escape(item['summary'])}</p>"
            f"<p><b>与你的关系：</b>{escape(item['why_it_matters'])}</p>"
            f"<p>{escape(item['source'])} · {escape(date_labels(item))}</p>"
        )
        if item["url"]:
            blocks.append(f'<p><a href="{escape(item["url"], quote=True)}">阅读原文</a></p>')
    for warning in brief["warnings"]:
        text += "\n来源提示：" + warning
        blocks.append(f"<p>来源提示：{escape(warning)}</p>")
    return (
        text,
        '<html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;padding:24px">'
        + "".join(blocks)
        + "</body></html>",
    )


def send_email(settings, user, run):
    if not settings.smtp_host or not settings.smtp_from:
        raise ValueError("未配置 SMTP_HOST / SMTP_FROM")
    message = EmailMessage()
    message["Subject"] = run["brief"]["title"]
    message["From"] = settings.smtp_from
    message["To"] = user["email"]
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(idstring=run["id"], domain="daily-ai.local")
    text, html = render_email(run["brief"])
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    if settings.smtp_security not in {"ssl", "starttls", "plain"}:
        raise ValueError("SMTP_SECURITY 应为 ssl / starttls / plain")
    factory = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
    kwargs = {"context": context} if settings.smtp_security == "ssl" else {}
    with factory(settings.smtp_host, settings.smtp_port, timeout=15, **kwargs) as smtp:
        if settings.smtp_security == "starttls":
            smtp.starttls(context=context)
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
