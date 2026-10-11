from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import CONTENT_SCHEMA, Base


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"
    __table_args__ = ({"schema": CONTENT_SCHEMA},)

    query_norm: Mapped[str] = mapped_column(Text, primary_key=True)
    query_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    geocoded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    miss_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
