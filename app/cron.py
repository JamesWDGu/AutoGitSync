"""极简 5 字段 cron 表达式解析器（零第三方依赖）。

字段顺序：``分 时 日 月 周``，支持 ``*`` / ``,`` / ``-`` / ``/`` 语法，
月份与星期支持英文缩写（JAN..DEC / SUN..SAT），并支持常用别名：

    @yearly @annually @monthly @weekly @daily @midnight @hourly @minutely

日与星期的组合遵循 Vixie cron 语义：当两者都被限定时，满足其一即可（OR）。
时间按「分钟」精度对齐，秒与微秒会被忽略。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Dict, Optional, Tuple

__all__ = ["Cron", "CronError"]


class CronError(ValueError):
    """cron 表达式非法。"""


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

# (字段名, 最小值, 最大值, 名称表, 是否为星期字段)
_FIELDS = (
    ("分钟", 0, 59, None, False),
    ("小时", 0, 23, None, False),
    ("日", 1, 31, None, False),
    ("月", 1, 12, _MONTH_NAMES, False),
    ("星期", 0, 7, _DOW_NAMES, True),
)

_MAX_SEARCH_DAYS = 366 * 5


def _parse_value(token: str, names: Optional[Dict[str, int]], lo: int, hi: int, field: str) -> int:
    text = token.strip().lower()
    if names and text in names:
        return names[text]
    if not re.fullmatch(r"\d{1,2}", text):
        raise CronError("字段「%s」中的 %r 不是合法数值" % (field, token.strip()))
    value = int(text)
    if value < lo or value > hi:
        raise CronError("字段「%s」的值 %d 超出范围 %d-%d" % (field, value, lo, hi))
    return value


def _parse_field(text: str, lo: int, hi: int, names: Optional[Dict[str, int]],
                 field: str, is_dow: bool) -> Tuple[int, ...]:
    values = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise CronError("字段「%s」存在空的取值" % field)

        step = 1
        if "/" in chunk:
            base, _, step_text = chunk.partition("/")
            step_text = step_text.strip()
            if not re.fullmatch(r"\d+", step_text) or int(step_text) == 0:
                raise CronError("字段「%s」中的步长 %r 非法（必须为正整数）" % (field, step_text))
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
                raise CronError("字段「%s」的区间 %r 起始值大于结束值" % (field, chunk))
        else:
            start = _parse_value(chunk, names, lo, hi, field)
            # `a/n` 表示从 a 到字段上界，步长为 n
            end = hi if has_step else start

        values.update(range(start, end + 1, step))

    if not values:
        raise CronError("字段「%s」没有解析出任何取值" % field)
    if is_dow and 7 in values:
        values.discard(7)
        values.add(0)
    return tuple(sorted(values))


class Cron:
    """一个已解析的 5 字段 cron 表达式。"""

    def __init__(self, expression: str) -> None:
        raw = " ".join(str(expression).split())
        if not raw:
            raise CronError("cron 表达式为空")
        if raw.startswith("@"):
            alias = raw.lower()
            if alias not in _ALIASES:
                raise CronError("不支持的别名 %r，可用：%s" % (raw, ", ".join(sorted(_ALIASES))))
            raw = _ALIASES[alias]

        parts = raw.split(" ")
        if len(parts) != 5:
            hint = "（本服务只支持 5 字段：分 时 日 月 周）" if len(parts) == 6 else ""
            raise CronError("cron 表达式需要 5 个字段，实际 %d 个：%r%s" % (len(parts), expression, hint))

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

    # -- 匹配 ---------------------------------------------------------------
    def _day_matches(self, day: dt.date) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days
        dow_ok = ((day.weekday() + 1) % 7) in self.dows  # Python: 周一=0 -> cron: 周日=0
        if self._dom_restricted and self._dow_restricted:
            return dom_ok or dow_ok
        if self._dom_restricted:
            return dom_ok
        if self._dow_restricted:
            return dow_ok
        return True

    def matches(self, when: dt.datetime) -> bool:
        """``when`` 是否正好命中该表达式（分钟精度）。"""
        if when.minute not in self.minutes or when.hour not in self.hours:
            return False
        return self._day_matches(when.date())

    def next_after(self, when: dt.datetime) -> dt.datetime:
        """返回严格晚于 ``when`` 的下一个触发时刻。"""
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
        raise CronError("在 %d 天内未找到匹配时间：%s" % (_MAX_SEARCH_DAYS, self.expression))

    def __str__(self) -> str:
        return self.expression

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "Cron(%r)" % self.expression
