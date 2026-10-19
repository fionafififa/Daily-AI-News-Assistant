import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Subscription(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    role: str = Field(default="工程师", max_length=160)
    email: str = Field(default="", max_length=254)
    topics: list[str] = Field(default_factory=lambda: ["AI 编程", "Agent", "开源模型"], max_length=12)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    excludes: list[str] = Field(default_factory=list, max_length=20)
    delivery_time: str = "08:00"
    timezone: str = "Asia/Shanghai"
    max_items: int = Field(default=5, ge=1, le=10)
    enabled: bool = True
    channel: Literal["inbox", "email"] = "inbox"

    @field_validator("topics", "keywords", "excludes")
    @classmethod
    def clean_terms(cls, values):
        if any(len(x) > 80 for x in values):
            raise ValueError("每个词条最多 80 字")
        return list(dict.fromkeys(x.strip() for x in values if x.strip()))

    @field_validator("delivery_time")
    @classmethod
    def valid_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("时间格式应为 HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("无效的 IANA 时区") from exc
        return value

    @model_validator(mode="after")
    def valid_preferences(self):
        if not self.topics and not self.keywords:
            raise ValueError("至少填写一个关注话题或关键词")
        if self.email and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", self.email):
            raise ValueError("邮箱格式不正确")
        if self.channel == "email" and not self.email:
            raise ValueError("邮件推送需要邮箱")
        return self


class Selection(BaseModel):
    article_id: str
    summary: str = Field(min_length=10, max_length=700)
    why_it_matters: str = Field(min_length=5, max_length=400)
    topic: str = Field(min_length=1, max_length=50)
    relevance: int = Field(ge=2, le=3, description="2=directly relevant, 3=strongly relevant")


class BriefSubmission(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    overview: str = Field(min_length=5, max_length=500)
    selections: list[Selection] = Field(max_length=10)
