"""Sunrise/Sunset-Logik für zeitgesteuerte Webcam-Fenster."""
from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun


def _tz(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Berlin")


def solar_window(
    lat: float, lon: float, tz_name: str, when: Optional[datetime] = None,
    offset_min: int = 0,
) -> Tuple[datetime, datetime]:
    """Liefert (sunrise, sunset) als tz-aware datetimes für den Tag von 'when'.

    offset_min: positiv = Fenster verkleinern (später aufgehen, früher untergehen),
                negativ = Fenster vergrößern.
    """
    tz = _tz(tz_name)
    now = when or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    loc = LocationInfo(latitude=lat, longitude=lon, timezone=tz_name)
    s = sun(loc.observer, date=now.date(), tzinfo=tz)
    sunrise = s["sunrise"] + timedelta(minutes=offset_min)
    sunset = s["sunset"] - timedelta(minutes=offset_min)
    return sunrise, sunset


def in_solar_window(
    lat: float, lon: float, tz_name: str, when: Optional[datetime] = None,
    offset_min: int = 0,
) -> bool:
    tz = _tz(tz_name)
    now = when or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    sunrise, sunset = solar_window(lat, lon, tz_name, now, offset_min)
    return sunrise <= now <= sunset


def in_clock_window(time_start: str, time_end: str, when: datetime) -> bool:
    """Prüft, ob 'when' im Zeitfenster (HH:MM bis HH:MM) liegt.

    Leere Strings = unbegrenzt. Fenster über Mitternacht wird unterstützt.
    """
    if not time_start and not time_end:
        return True

    def parse(s: str, fallback: time) -> time:
        if not s:
            return fallback
        h, m = s.split(":")
        return time(int(h), int(m))

    start = parse(time_start, time(0, 0))
    end = parse(time_end, time(23, 59))
    t = when.time()
    if start <= end:
        return start <= t <= end
    # Über Mitternacht
    return t >= start or t <= end


WEEKDAY_LABELS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def in_weekdays(weekdays: str, when: datetime) -> bool:
    """weekdays = "1111100" → Mo-Fr aktiv."""
    if not weekdays or len(weekdays) != 7:
        return True
    idx = when.weekday()  # 0 = Monday
    return weekdays[idx] == "1"
