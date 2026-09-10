"""5 字段 cron 解析器测试。"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from cron import Cron, CronError  # noqa: E402


class CronParseTest(unittest.TestCase):
    def test_every_minute(self):
        cron = Cron("* * * * *")
        self.assertTrue(cron.matches(dt.datetime(2024, 5, 6, 13, 37)))
        self.assertEqual(cron.next_after(dt.datetime(2024, 5, 6, 13, 37, 30)),
                         dt.datetime(2024, 5, 6, 13, 38))

    def test_step(self):
        cron = Cron("*/15 * * * *")
        self.assertEqual(cron.minutes, (0, 15, 30, 45))
        self.assertEqual(cron.next_after(dt.datetime(2024, 5, 6, 13, 0)),
                         dt.datetime(2024, 5, 6, 13, 15))
        self.assertEqual(cron.next_after(dt.datetime(2024, 5, 6, 13, 46)),
                         dt.datetime(2024, 5, 6, 14, 0))

    def test_range_list_and_names(self):
        cron = Cron("0,30 9-17 * * 1-5")
        self.assertEqual(cron.minutes, (0, 30))
        self.assertEqual(cron.hours, (9, 10, 11, 12, 13, 14, 15, 16, 17))
        self.assertEqual(cron.dows, (1, 2, 3, 4, 5))
        self.assertFalse(cron.matches(dt.datetime(2024, 5, 5, 9, 0)))   # 周日
        self.assertTrue(cron.matches(dt.datetime(2024, 5, 6, 9, 0)))    # 周一

        named = Cron("0 0 * JAN MON")
        self.assertEqual(named.months, (1,))
        self.assertEqual(named.dows, (1,))

    def test_alias_and_sunday_seven(self):
        self.assertEqual(str(Cron("@daily")), "0 0 * * *")
        self.assertEqual(str(Cron("@hourly")), "0 * * * *")
        self.assertEqual(Cron("0 0 * * 7").dows, (0,))

    def test_dow_from_after_step(self):
        self.assertEqual(Cron("0 0 * * 1/2").dows, (0, 1, 3, 5))

    def test_next_after_weekday_restriction(self):
        cron = Cron("0 0 * * 1")
        self.assertEqual(cron.next_after(dt.datetime(2024, 1, 1, 0, 0)),
                         dt.datetime(2024, 1, 8, 0, 0))

    def test_next_after_monthly(self):
        cron = Cron("@monthly")
        self.assertEqual(cron.next_after(dt.datetime(2024, 1, 15, 8, 0)),
                         dt.datetime(2024, 2, 1, 0, 0))

    def test_dom_and_dow_are_or(self):
        # 1 号或周一都触发（Vixie cron 语义）
        cron = Cron("0 0 1 * 1")
        self.assertEqual(cron.next_after(dt.datetime(2024, 1, 2, 0, 0)),
                         dt.datetime(2024, 1, 8, 0, 0))   # 下周一
        self.assertEqual(cron.next_after(dt.datetime(2024, 1, 30, 0, 0)),
                         dt.datetime(2024, 2, 1, 0, 0))   # 2 月 1 日

    def test_next_after_keeps_timezone(self):
        tz = dt.timezone(dt.timedelta(hours=8))
        cron = Cron("30 6 * * *")
        nxt = cron.next_after(dt.datetime(2024, 5, 6, 7, 0, tzinfo=tz))
        self.assertEqual(nxt, dt.datetime(2024, 5, 7, 6, 30, tzinfo=tz))

    def test_seconds_are_truncated(self):
        cron = Cron("*/5 * * * *")
        self.assertEqual(cron.next_after(dt.datetime(2024, 5, 6, 13, 4, 59)),
                         dt.datetime(2024, 5, 6, 13, 5))

    def test_invalid_expressions(self):
        for expression in ("", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *",
                           "* * 0 * *", "* * * 13 *", "* * * * 8", "*/0 * * * *",
                           "a * * * *", "5-1 * * * *", "@nope", "* * * * ,"):
            with self.subTest(expression=expression):
                with self.assertRaises(CronError):
                    Cron(expression)

    def test_question_mark_alias(self):
        self.assertEqual(Cron("0 0 ? * *").days, tuple(range(1, 32)))


if __name__ == "__main__":
    unittest.main()
