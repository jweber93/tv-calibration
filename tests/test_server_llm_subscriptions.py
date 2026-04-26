"""Tests for LLM pub-sub concurrency primitives (_llm_subscribe/unsubscribe/broadcast)."""

import queue
import threading
import time
import unittest

from server import (
    _llm_broadcast,
    _llm_queues,
    _llm_queues_lock,
    _llm_subscribe,
    _llm_unsubscribe,
)


def _reset_llm_queues():
    """Clear all LLM queues for test isolation."""
    with _llm_queues_lock:
        _llm_queues.clear()


class TestLlmSubscribeUnsubscribeBroadcast(unittest.TestCase):
    """Tests for the _llm_subscribe / _llm_unsubscribe / _llm_broadcast trio."""

    def setUp(self):
        _reset_llm_queues()

    def tearDown(self):
        _reset_llm_queues()

    def test_concurrent_subscribe_unsubscribe(self):
        """20 threads calling subscribe/broadcast/unsubscribe should all complete without error."""
        sid = "test-concurrent"
        results = []

        def worker():
            q = _llm_subscribe(sid)
            _llm_broadcast(sid, {"event": "ping"})
            _llm_unsubscribe(sid, q)
            results.append("ok")

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(results), 20)

    def test_subscribe_broadcast_unsubscribe(self):
        """Single-threaded happy path: subscribe, broadcast, receive, unsubscribe."""
        sid = "test-happy-path"
        q = _llm_subscribe(sid)

        _llm_broadcast(sid, {"event": "ping", "data": 42})
        msg = q.get(timeout=5)
        self.assertEqual(msg, {"event": "ping", "data": 42})

        _llm_unsubscribe(sid, q)

        with self.assertRaises(queue.Empty):
            q.get(timeout=1)

    def test_unsubscribe_nonexistent_queue(self):
        """Unsubscribing a queue that was never subscribed should not raise."""
        sid = "test-nonexistent"
        fake_q = queue.Queue()
        _llm_unsubscribe(sid, fake_q)
        # Should reach here without raising
        self.assertTrue(True)

    def test_broadcast_to_no_subscribers(self):
        """Broadcasting to a session with no subscribers should not raise."""
        sid = "test-no-subscribers"
        _llm_broadcast(sid, {"event": "ping"})
        # Should reach here without raising
        self.assertTrue(True)

    def test_queue_capacity_overflow(self):
        """When a queue is full, broadcast should drop the event with a warning, not crash."""
        sid = "test-full-queue"
        q = _llm_subscribe(sid)

        # Fill the queue (maxsize=100)
        for i in range(100):
            _llm_broadcast(sid, {"event": "fill", "i": i})

        # Next broadcast should not raise, even though queue is full
        _llm_broadcast(sid, {"event": "overflow"})
        # Should reach here without raising
        self.assertTrue(True)

    def test_multiple_subscribers_receive_broadcast(self):
        """Multiple subscribers for the same session all receive the broadcast."""
        sid = "test-multi-sub"
        q1 = _llm_subscribe(sid)
        q2 = _llm_subscribe(sid)

        _llm_broadcast(sid, {"event": "multi"})

        msg1 = q1.get(timeout=5)
        msg2 = q2.get(timeout=5)
        self.assertEqual(msg1, {"event": "multi"})
        self.assertEqual(msg2, {"event": "multi"})

        _llm_unsubscribe(sid, q1)
        _llm_unsubscribe(sid, q2)

    def test_unsubscribe_cleans_up_empty_session(self):
        """After unsubscribing the last listener, the session key should be removed."""
        sid = "test-cleanup"
        q = _llm_subscribe(sid)
        self.assertIn(sid, _llm_queues)

        _llm_unsubscribe(sid, q)
        self.assertNotIn(sid, _llm_queues)

    def test_unsubscribe_removes_only_one_queue(self):
        """Unsubscribing one queue should not affect other queues for the same session."""
        sid = "test-partial-unsub"
        q1 = _llm_subscribe(sid)
        q2 = _llm_subscribe(sid)

        self.assertEqual(len(_llm_queues[sid]), 2)

        _llm_unsubscribe(sid, q1)
        self.assertEqual(len(_llm_queues[sid]), 1)
        self.assertIn(q2, _llm_queues[sid])

        _llm_unsubscribe(sid, q2)
        self.assertNotIn(sid, _llm_queues)

    def test_subscribe_creates_maxsize_100_queue(self):
        """Each subscribe call creates a queue with maxsize=100."""
        q = _llm_subscribe("test-maxsize")
        self.assertEqual(q.maxsize, 100)
        _llm_unsubscribe("test-maxsize", q)


if __name__ == "__main__":
    unittest.main()
