"""Loki 推送失败时限频在控制台记 WARNING，不再只往 stderr 打 traceback。"""

import logging
import unittest
from unittest.mock import patch

from true_love_common.observability import logging as logging_config


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class LokiFailureReporterTests(unittest.TestCase):
    def test_reports_once_per_interval_with_the_dropped_count(self):
        console = _Collect()
        reporter = logging_config._LokiFailureReporter("t", console)
        with patch.object(logging_config.time, "monotonic", side_effect=[100, 110, 120, 200]):
            for _ in range(4):
                reporter.failed("推送出错", ValueError("401"))
        self.assertEqual(len(console.records), 2)
        self.assertEqual(console.records[0].levelno, logging.WARNING)
        self.assertIn("丢了 1 条", console.records[0].getMessage())
        self.assertIn("丢了 3 条", console.records[1].getMessage())


if __name__ == "__main__":
    unittest.main()
