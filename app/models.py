from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, UniqueConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Discogs personal access token (optional). Stored per-user so multi-user hosting works.
    discogs_token: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Email activation for hosted deployments.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    collections_owned: Mapped[list["Collection"]] = relationship(back_populates="owner")
    shares: Mapped[list["CollectionShare"]] = relationship(back_populates="user")

class Collection(Base):
    __tablename__ = "collections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped["User"] = relationship(back_populates="collections_owned")
    shares: Mapped[list["CollectionShare"]] = relationship(back_populates="collection", cascade="all, delete-orphan")
    records: Mapped[list["Record"]] = relationship(back_populates="collection", cascade="all, delete-orphan")

class CollectionShare(Base):
    __tablename__ = "collection_shares"
    __table_args__ = (UniqueConstraint("collection_id", "user_id", name="uq_collection_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="editor")  # viewer/editor

    collection: Mapped["Collection"] = relationship(back_populates="shares")
    user: Mapped["User"] = relationship(back_populates="shares")

class Record(Base):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id"), index=True)
    discogs_release_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    artist: Mapped[str | None] = mapped_column(String(300), nullable=True)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    catno: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    formats_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracklist_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    collection: Mapped["Collection"] = relationship(back_populates="records")
    photos: Mapped[list["Photo"]] = relationship(back_populates="record", cascade="all, delete-orphan")

class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # discogs/upload
    url: Mapped[str | None] = mapped_column(Text, nullable=True)      # discogs URL
    filename: Mapped[str | None] = mapped_column(String(300), nullable=True)  # upload filename
    label: Mapped[str | None] = mapped_column(String(40), nullable=True)  # front/back/other

    record: Mapped["Record"] = relationship(back_populates="photos")