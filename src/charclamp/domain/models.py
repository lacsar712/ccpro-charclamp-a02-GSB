from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="worker")


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    clamps: Mapped[list[Clamp]] = relationship(back_populates="site", cascade="all, delete-orphan")


class Clamp(Base):
    __tablename__ = "clamps"
    __table_args__ = (UniqueConstraint("site_id", "code", name="uq_clamp_code_per_site"),)

    STATUS_STACKED = "stacked"
    STATUS_BURNING = "burning"
    STATUS_DRAWN = "drawn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_STACKED)
    wood_species: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    site: Mapped[Site] = relationship(back_populates="clamps")
    shifts: Mapped[list[BurnShift]] = relationship(
        back_populates="clamp",
        cascade="all, delete-orphan",
    )
    weigh_slips: Mapped[list[WeighSlip]] = relationship(
        back_populates="clamp",
        cascade="all, delete-orphan",
    )


class BurnShift(Base):
    __tablename__ = "burn_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clamp_id: Mapped[int] = mapped_column(ForeignKey("clamps.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    peak_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    charcoal_grade: Mapped[str] = mapped_column(String(40), nullable=False, default="B")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    clamp: Mapped[Clamp] = relationship(back_populates="shifts")


class WeighSlip(Base):
    """出炭过磅联：出炭前须先落下一张当日有效（合格且未作废）联。"""

    __tablename__ = "weigh_slips"
    __table_args__ = (
        # 同一炭窑同一自然日，最多保存一张未作废的合格联。
        Index(
            "uq_weigh_slip_one_valid_per_clamp_day",
            "clamp_id",
            "weighed_on",
            unique=True,
            postgresql_where=text("is_qualified AND NOT voided"),
            sqlite_where=text("is_qualified AND NOT voided"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clamp_id: Mapped[int] = mapped_column(ForeignKey("clamps.id"), nullable=False)
    weighed_on: Mapped[date] = mapped_column(Date, nullable=False)
    gross_kg: Mapped[float] = mapped_column(Float, nullable=False)
    tare_kg: Mapped[float] = mapped_column(Float, nullable=False)
    weigher: Mapped[str] = mapped_column(String(64), nullable=False)
    is_qualified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    voided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    clamp: Mapped[Clamp] = relationship(back_populates="weigh_slips")

    @property
    def net_kg(self) -> float:
        """净重 = 毛重 - 皮重（千克）。"""
        return self.gross_kg - self.tare_kg

    @property
    def is_valid_for_draw(self) -> bool:
        """可用于出炭的联：合格且未作废。"""
        return bool(self.is_qualified) and not self.voided
