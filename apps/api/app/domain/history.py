from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.app.core.database import Base


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    competition_type: Mapped[str] = mapped_column(String(32), nullable=False, default="league")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    seasons: Mapped[list["Season"]] = relationship(back_populates="competition")
    matches: Mapped[list["Match"]] = relationship(back_populates="competition")


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("competition_id", "name", name="uq_seasons_competition_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    competition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("competitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    competition: Mapped[Competition] = relationship(back_populates="seasons")
    matches: Mapped[list["Match"]] = relationship(back_populates="season")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (Index("ix_teams_name", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    home_matches: Mapped[list["Match"]] = relationship(
        foreign_keys="Match.home_team_id",
        back_populates="home_team",
    )
    away_matches: Mapped[list["Match"]] = relationship(
        foreign_keys="Match.away_team_id",
        back_populates="away_team",
    )


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint(
            "season_id",
            "home_team_id",
            "away_team_id",
            "kickoff_at",
            name="uq_matches_fixture_identity",
        ),
        Index("ix_matches_kickoff_at", "kickoff_at"),
        Index("ix_matches_home_team_kickoff", "home_team_id", "kickoff_at"),
        Index("ix_matches_away_team_kickoff", "away_team_id", "kickoff_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    competition_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("competitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    season_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("seasons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    home_team_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    away_team_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("teams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    home_score: Mapped[int | None] = mapped_column(nullable=True)
    away_score: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    competition: Mapped[Competition] = relationship(back_populates="matches")
    season: Mapped[Season] = relationship(back_populates="matches")
    home_team: Mapped[Team] = relationship(
        foreign_keys=[home_team_id],
        back_populates="home_matches",
    )
    away_team: Mapped[Team] = relationship(
        foreign_keys=[away_team_id],
        back_populates="away_matches",
    )
