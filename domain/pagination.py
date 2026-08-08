"""Shared pagination helpers."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageParams(BaseModel):
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
    has_more: bool = False

    @classmethod
    def of(cls, items: list[T], *, total: int, limit: int, offset: int) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + len(items)) < total,
        )
