"""Tests for reddit_monitor request/proxy-failure handling.

Regression coverage for the 403 path, where a method-name mismatch
(`mark_proxy_failed` vs the real `mark_failed`) raised an AttributeError
that the broad `except Exception` silently swallowed — breaking proxy
backoff with zero visible symptoms.

Run:  python3 -m unittest test_reddit_monitor -v
"""
import unittest
from unittest import mock

import reddit_monitor as rm


class FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json


def _monitor_with_mock_pm():
    """Build a RedditMonitor without its heavy __init__ (config/db/api key),
    injecting a spec-locked proxy manager.

    The `spec=` is load-bearing: it makes the mock reject any attribute the
    real WebshareProxyManager does not define, so calling a nonexistent
    method (the original bug) raises AttributeError exactly as in production.
    A plain Mock() would silently auto-create `mark_proxy_failed` and hide it.
    """
    monitor = rm.RedditMonitor.__new__(rm.RedditMonitor)
    pm = mock.Mock(spec=rm.WebshareProxyManager)
    pm.get_random_proxy.return_value = {
        "proxy_address": "1.2.3.4",
        "port": "8080",
        "username": "u",
        "password": "p",
    }
    pm.get_proxy_dict.return_value = {"http": "http://x", "https": "http://x"}
    monitor.proxy_manager = pm
    return monitor, pm


class MakeRequestTest(unittest.TestCase):
    @mock.patch.object(rm.time, "sleep", lambda *a, **k: None)
    def test_403_marks_proxy_failed_without_swallowed_error(self):
        monitor, pm = _monitor_with_mock_pm()
        responses = [FakeResponse(403), FakeResponse(200, {"ok": True})]

        with mock.patch.object(rm.requests, "get", side_effect=responses):
            with self.assertLogs(rm.logger, level="DEBUG") as cm:
                result = monitor.make_request("http://reddit.test/r/x.json", max_retries=5)

        # Recovered on the retry after the 403
        self.assertEqual(result, {"ok": True})
        # The REAL failover method was invoked
        pm.mark_failed.assert_called_once()
        # No AttributeError leaked into the broad except (the original bug)
        self.assertFalse(
            any("Request error" in line for line in cm.output),
            msg="403 path leaked an exception into the broad except: %s" % cm.output,
        )

    @mock.patch.object(rm.time, "sleep", lambda *a, **k: None)
    def test_200_returns_json_and_skips_failover(self):
        monitor, pm = _monitor_with_mock_pm()
        with mock.patch.object(rm.requests, "get", return_value=FakeResponse(200, {"data": 1})):
            result = monitor.make_request("http://reddit.test/r/x.json")
        self.assertEqual(result, {"data": 1})
        pm.mark_failed.assert_not_called()

    @mock.patch.object(rm.time, "sleep", lambda *a, **k: None)
    def test_no_proxy_available_returns_none(self):
        monitor, pm = _monitor_with_mock_pm()
        pm.get_random_proxy.return_value = None
        with mock.patch.object(rm.requests, "get") as get:
            result = monitor.make_request("http://reddit.test/r/x.json")
        self.assertIsNone(result)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
