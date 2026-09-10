"""Minimal 5-field cron expression parser (no third-party dependency).

Field order: ``minute hour day month weekday``.  Supports ``*`` / ``,`` / ``-`` / ``/``,
month and weekday names (JAN..DEC / SUN..SAT) and the usual aliases:

    @yearly @annually @monthly @weekly @daily @midnight @hourly @minutely

Day-of-month and day-of-week follow Vixie cron semantics: when both are restricted,
matching either one is enough (OR).  Resolution is one minute; seconds are ignored.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Dict, Optional, Tuple

from i18n import t

__all__ = ["Cron", "CronError"]


class CronError(ValueError):
    """The cron expression is invalid."""


_MONTH_NAMES: Dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DOW_NAMES: Dict[str, int] = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

_ALIASES: Dict[str, str] = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
    "@minutely": "* * * * *",
}

# (field name, min, max, name table, is-day-of-week)
_FIELDS = (
    ("minute", 0, 59, None, False),
    ("hour", 0, 23, None, False),
    ("day of month", 1, 31, None, False),
    ("month", 1, 12, _MONTH_NAMES, False),
    ("day of week", 0, 7, _DOW_NAMES, True),
)

_MAX_SEARCH_DAYS = 366 * 5


def _parse_value(token: str, names: Optional[Dict[str, int]], lo: int, hi: int, field: str) -> int:
    field = t(field)          # cron field names appear in messages, translate them too
    text = token.strip().lower()
    if names and text in names:
        return names[text]
    if not re.fullmatch(r"\d{1,2}", text):
        raise CronError(t('Field "%s": %r is not a valid value', field, token.strip()))
    value = int(text)
    if value < lo or value > hi:
        raise CronError(t('Field "%s": %d is out of range %d-%d', field, value, lo, hi))
    return value


def _parse_field(text: str, lo: int, hi: int, names: Optional[Dict[str, int]],
                 field: str, is_dow: bool) -> Tuple[int, ...]:
    field = t(field)
    values = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise CronError(t('Field "%s" contains an empty value', field))

        step = 1
        if "/" in chunk:
            base, _, step_text = chunk.partition("/")
            step_text = step_text.strip()
            if not re.fullmatch(r"\d+", step_text) or int(step_text) == 0:
                raise CronError(t('Field "%s": step %r is invalid (must be a positive integer)',
                                  field, step_text))
            step = int(step_text)
            chunk = base.strip()
            has_step = True
        else:
            has_step = False

        if chunk in ("*", "?"):
            start, end = lo, hi
        elif "-" in chunk:
            left, _, right = chunk.partition("-")
            start = _parse_value(left, names, lo, hi, field)
            end = _parse_value(right, names, lo, hi, field)
            if start > end:
                raise CronError(t('Field "%s": range %r starts after it ends', field, chunk))
        else:
            start = _parse_value(chunk, names, lo, hi, field)
            # `a/n` means: from a up to the field maximum, stepping by n
            end = hi if has_step else start

        values.update(range(start, end + 1, step))

    if not values:
        raise CronError(t('Field "%s" did not resolve to any value', field))
    if is_dow and 7 in values:
        values.discard(7)
        values.add(0)
    return tuple(sorted(values))


class Cron:
    """A parsed 5-field cron expression."""

    def __init__(self, expression: str) -> None:
        raw = " ".join(str(expression).split())
        if not raw:
            raise CronError(t("cron expression is empty"))
        if raw.startswith("@"):
            alias = raw.lower()
            if alias not in _ALIASES:
                raise CronError(t("unsupported alias %r, available: %s",
                                  raw, ", ".join(sorted(_ALIASES))))
            raw = _ALIASES[alias]

        parts = raw.split(" ")
        if len(parts) != 5:
            hint = t(" (this service only supports 5 fields: minute hour day month weekday)") \
                if len(parts) == 6 else ""
            raise CronError(t("cron expression needs 5 fields, got %d: %r%s",
                              len(parts), expression, hint))

        parsed = [_parse_field(parts[index], lo, hi, names, name, is_dow)
                  for index, (name, lo, hi, names, is_dow) in enumerate(_FIELDS)]
        self.expression = raw
        self.minutes: Tuple[int, ...] = parsed[0]
        self.hours: Tuple[int, ...] = parsed[1]
        self.days: Tuple[int, ...] = parsed[2]
        self.months: Tuple[int, ...] = parsed[3]
        self.dows: Tuple[int, ...] = parsed[4]
        self._dom_restricted = parts[2].strip() != "*"
        self._dow_restricted = parts[4].strip() != "*"

    # -- matching -----------------------------------------------------------
    def _day_matches(self, day: dt.date) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days
        dow_ok = ((day.weekday() + 1) % 7) in self.dows  # Python Mon=0 -> cron Sun=0
        if self._dom_restricted and self._dow_restricted:
            return dom_ok or dow_ok
        if self._dom_restricted:
            return dom_ok
        if self._dow_restricted:
            return dow_ok
        return True

    def matches(self, when: dt.datetime) -> bool:
        """Whether ``when`` matches this expression (minute resolution)."""
        if when.minute not in self.minutes or when.hour not in self.hours:
            return False
        return self._day_matches(when.date())

    def next_after(self, when: dt.datetime) -> dt.datetime:
        """Next trigger strictly after ``when``."""
        start = when.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        tzinfo = when.tzinfo
        day = start.date()
        for _ in range(_MAX_SEARCH_DAYS):
            if self._day_matches(day):
                for hour in self.hours:
                    for minute in self.minutes:
                        candidate = dt.datetime.combine(day, dt.time(hour, minute))
                        if tzinfo is not None:
                            candidate = candidate.replace(tzinfo=tzinfo)
                        if candidate >= start:
                            return candidate
            day += dt.timedelta(days=1)
        raise CronError(t("no matching time found within %d days: %s",
                          _MAX_SEARCH_DAYS, self.expression))

    def __str__(self) -> str:
        return self.expression

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Cron(%r)" % self.expression
