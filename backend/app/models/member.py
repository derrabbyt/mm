import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Member(Base):
    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    latitude: Mapped[float] = mapped_column(nullable=True)

    longitude: Mapped[float] = mapped_column(nullable=True)
    