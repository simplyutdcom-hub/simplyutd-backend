"""Pydantic request/response models for the SimplyUtd API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

NewsStatus = Literal["Published", "Draft"]


class ORMModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --- News ------------------------------------------------------------------
class NewsBase(ORMModel):
    title: str = Field(min_length=3, max_length=300)
    summary: str | None = None
    content: str | None = None
    image: str | None = None
    source: str | None = None
    source_url: str | None = None
    category: str = "News"
    tags: list[str] = Field(default_factory=list)
    author: str | None = None
    status: NewsStatus = "Draft"
    featured: bool = False


class NewsCreate(NewsBase):
    pass


class NewsUpdate(ORMModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    summary: str | None = None
    content: str | None = None
    image: str | None = None
    source: str | None = None
    source_url: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    author: str | None = None
    status: NewsStatus | None = None
    featured: bool | None = None


class NewsOut(NewsBase):
    id: str
    slug: str | None = None
    views: int = 0
    comments: int = 0
    external: bool = False
    published_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- Products --------------------------------------------------------------
class ProductBase(ORMModel):
    name: str = Field(min_length=2, max_length=200)
    category: str = "Accessories"
    brand: str | None = None
    price: str = "$34.99"
    image: str | None = None
    description: str | None = None
    status: NewsStatus = "Published"
    featured: bool = False


class ProductCreate(ProductBase):
    pass


class ProductUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    category: str | None = None
    brand: str | None = None
    price: str | None = None
    image: str | None = None
    description: str | None = None
    status: NewsStatus | None = None
    featured: bool | None = None


class ProductOut(ProductBase):
    id: str
    slug: str | None = None
    clicks: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- Messages / contact ----------------------------------------------------
class ContactCreate(ORMModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    type: str = Field(default="General question", max_length=80)
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=5000)


class MessageOut(ContactCreate):
    id: str
    initials: str = "??"
    unread: bool = True
    favourite: bool = False
    label: str | None = None
    replies: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None


class MessageUpdate(ORMModel):
    unread: bool | None = None
    favourite: bool | None = None
    label: str | None = None


class MessageReply(ORMModel):
    body: str = Field(min_length=1, max_length=5000)


class ContactResponse(ORMModel):
    ok: bool = True
    message: str = "Thanks — we'll be in touch soon."
    id: str


# --- Newsletter ------------------------------------------------------------
class SubscribeCreate(ORMModel):
    email: EmailStr
    source: str | None = None


class SubscribeResponse(ORMModel):
    ok: bool = True
    message: str
    email: EmailStr
    already_subscribed: bool = False


class SubscriberOut(ORMModel):
    id: str
    email: EmailStr
    source: str | None = None
    created_at: datetime | None = None


# --- Comments --------------------------------------------------------------
CommentTarget = Literal["story", "live"]
CommentSort = Literal["newest", "oldest", "top"]


class CommentCreate(ORMModel):
    target_type: CommentTarget
    target_id: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=2000)
    parent_id: str | None = Field(default=None, max_length=120)
    author: str | None = Field(default=None, max_length=60)
    visitor_id: str | None = Field(default=None, max_length=80)


class CommentLike(ORMModel):
    visitor_id: str = Field(min_length=1, max_length=80)


class CommentOut(ORMModel):
    id: str
    target_type: str | None = None
    target_id: str | None = None
    parent_id: str | None = None
    author: str | None = None
    initials: str | None = None
    body: str
    likes: int = 0
    liked: bool = False
    mine: bool = False
    pinned: bool = False
    created_at: datetime | None = None
    replies: list["CommentOut"] = Field(default_factory=list)


class CommentThreadOut(ORMModel):
    items: list[CommentOut]
    total: int
    target_type: str
    target_id: str
    sort: str = "newest"


# --- Hub -------------------------------------------------------------------
class HubSectionUpsert(ORMModel):
    data: Any


# --- Auth ------------------------------------------------------------------
class RegisterCreate(ORMModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str | None = Field(default=None, max_length=60)


class LoginCreate(ORMModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORMModel):
    id: str
    email: EmailStr
    name: str | None = None
    initials: str = "RD"
    created_at: datetime | None = None


class AuthResponse(ORMModel):
    ok: bool = True
    token: str
    user: UserOut


class MeResponse(ORMModel):
    user: UserOut | None = None


# --- Analytics -------------------------------------------------------------
class AnalyticsEvent(ORMModel):
    type: str = Field(default="page_view", max_length=40)
    path: str | None = None
    label: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class AnalyticsResponse(ORMModel):
    ok: bool = True


# --- News sources (RSS ingestion) ------------------------------------------
class NewsSourceBase(ORMModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=500)
    enabled: bool = True


class NewsSourceCreate(NewsSourceBase):
    pass


class NewsSourceUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=8, max_length=500)
    enabled: bool | None = None


class NewsSourceOut(NewsSourceBase):
    id: str
    builtin: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- Generic ---------------------------------------------------------------
class ListResponse(ORMModel):
    items: list[Any]
    total: int
    limit: int | None = None
    skip: int | None = None


class DeleteResponse(ORMModel):
    ok: bool = True
    id: str
