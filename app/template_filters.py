"""Jinja2-Filter fuer die Templates."""
from __future__ import annotations

from datetime import datetime, timezone


def _to_local(dt):
    """Naive UTC-datetime -> lokales aware datetime (Server-TZ)."""
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except Exception:
        return dt


def localfmt(dt, fmt: str = "%d.%m. %H:%M") -> str:
    """Formatiert ein naives UTC-datetime in lokaler Server-Zeit."""
    if dt is None:
        return ""
    local = _to_local(dt)
    try:
        return local.strftime(fmt)
    except Exception:
        return ""


def humanize_relative(dt) -> str:
    """Gibt eine relative Zeitangabe wie 'vor 12 Min.' / 'vor 2:35 Std.' zurueck.

    Erwartet ein datetime-Objekt in UTC (so wie scheduler.run_cam es schreibt).
    """
    if dt is None:
        return ""
    try:
        now = datetime.utcnow()
        if dt > now:
            return "in Kuerze"
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 5:
            return "gerade eben"
        if seconds < 60:
            return f"vor {seconds} Sek."
        minutes = seconds // 60
        if minutes < 60:
            return f"vor {minutes} Min."
        hours = minutes // 60
        rem_min = minutes - hours * 60
        if hours < 24:
            return f"vor {hours}:{rem_min:02d} Std."
        days = hours // 24
        rem_h = hours - days * 24
        if days < 7:
            return f"vor {days} Tg. {rem_h} Std."
        weeks = days // 7
        if weeks < 5:
            return f"vor {weeks} Wo."
        months = days // 30
        if months < 12:
            return f"vor {months} Mon."
        years = days // 365
        return f"vor {years} J."
    except Exception:
        return ""
