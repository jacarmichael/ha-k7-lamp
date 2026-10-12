"""Per-lamp program and its validation (no Home Assistant imports).

With a program, the target schedule lives in HA and the lamp gets a daily plan built
from it (target -> acclimation scaling -> moon overlay, see effective.py), written by
the rules in planner.py. A Program is valid by construction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from .protocol import Row, sort_rows, validate_schedule

DEFAULT_MOON = {
    "enabled": True,
    "max_level": 15,
    "channels": {"1": 100, "4": 70},  # Royal Blue full, Cyan at 70% (as bitbarista)
    "follow_moon": True,
}


def validate_moon(config: dict[str, Any]) -> dict[str, Any]:
    """Validate before storage AND before indexing a schedule row."""
    if not isinstance(config, dict):
        raise ValueError("Moon settings must be an object")
    cfg = {**DEFAULT_MOON, **config}
    if type(cfg["enabled"]) is not bool or type(cfg["follow_moon"]) is not bool:
        raise ValueError("Moon enabled/follow_moon must be booleans")
    if type(cfg["max_level"]) is not int or not 1 <= cfg["max_level"] <= 50:
        raise ValueError("Maximum moon level must be 1-50%")
    if not isinstance(cfg["channels"], dict):
        raise ValueError("Moon channels must be an object")
    weights = {}
    for key, value in cfg["channels"].items():
        if str(key) not in {"0", "1", "2", "3", "4", "5"}:
            raise ValueError("Moon channel must be an integer from 0 to 5")
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError("Moon channel weight must be 0-100%")
        if value:
            weights[str(key)] = value
    return {**cfg, "channels": weights}


def _check_time(value: str | None, what: str) -> None:
    if value is not None and datetime.fromisoformat(value).tzinfo is None:
        raise ValueError(f"{what} must include a timezone")


@dataclass(frozen=True)
class Acclimation:
    start_pct: int
    days: int
    started: date

    def __post_init__(self) -> None:
        if type(self.start_pct) is not int or not 1 <= self.start_pct <= 99:
            raise ValueError("Acclimation start must be 1-99%")
        if type(self.days) is not int or not 1 <= self.days <= 120:
            raise ValueError("Acclimation duration must be 1-120 days")
        if not isinstance(self.started, date):
            raise ValueError("Acclimation start date is missing")


@dataclass(frozen=True)
class Applied:
    """The last plan confirmed on the lamp by reading it back."""

    cycle: str | None  # start of the daily write window it was built for (ISO); None without one
    percent: int  # acclimation percentage it was built for; 100 without acclimation
    at: str | None = None  # when it was confirmed (ISO); None if carried over from v0.5.1

    def __post_init__(self) -> None:
        if type(self.percent) is not int or not 1 <= self.percent <= 100:
            raise ValueError("Applied percentage must be 1-100%")
        _check_time(self.cycle, "Applied plan cycle")
        _check_time(self.at, "Applied plan time")


@dataclass(frozen=True)
class Program:
    target_manual: list[int]
    target_schedule: list[Row]
    acclimation: Acclimation | None = None
    moon: dict[str, Any] | None = None
    pending: bool = False  # changed since the last confirmed plan
    applied: Applied | None = None
    written_cycle: str | None = None  # cycle with an acknowledged ALL_SET

    def __post_init__(self) -> None:
        validate_schedule(self.target_manual, self.target_schedule)
        object.__setattr__(self, "target_manual", list(self.target_manual))
        object.__setattr__(self, "target_schedule", sort_rows(self.target_schedule))
        if self.moon is not None:
            object.__setattr__(self, "moon", validate_moon(self.moon))
        if type(self.pending) is not bool:
            raise ValueError("Program pending flag must be a boolean")
        _check_time(self.written_cycle, "Written plan cycle")

    @property
    def moon_enabled(self) -> bool:
        return bool(self.moon and self.moon["enabled"])

    @property
    def needed(self) -> bool:
        """Without acclimation or moonlight there is nothing to layer on the target."""
        return self.acclimation is not None or self.moon_enabled

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Program:
        acc = data.get("acclimation")
        applied = data.get("applied")
        return cls(
            target_manual=data["target_manual"],
            target_schedule=data["target_schedule"],
            acclimation=Acclimation(acc["start_pct"], acc["days"], date.fromisoformat(acc["started"]))
            if acc else None,
            moon=data.get("moon"),
            pending=data.get("pending", False),
            applied=Applied(applied.get("cycle"), applied["percent"], applied.get("at")) if applied else None,
            written_cycle=data.get("written_cycle"),
        )

    def to_dict(self) -> dict[str, Any]:
        acc = self.acclimation
        return {
            "target_manual": list(self.target_manual),
            "target_schedule": [list(r) for r in self.target_schedule],
            "acclimation": {"start_pct": acc.start_pct, "days": acc.days, "started": acc.started.isoformat()}
            if acc else None,
            "moon": {**self.moon, "channels": dict(self.moon["channels"])} if self.moon else None,
            "pending": self.pending,
            "applied": asdict(self.applied) if self.applied else None,
            "written_cycle": self.written_cycle,
        }
