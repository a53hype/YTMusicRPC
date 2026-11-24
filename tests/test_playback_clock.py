import queue
import unittest
from unittest.mock import patch

import app


class FakeRPC:
    def __init__(self):
        self.calls = []

    def clear(self):
        self.calls.append(("clear", {}))

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))

    def close(self):
        pass


def make_track(sampled_at=1_000_000, position=20.0, playing=True):
    return {
        "action": "state",
        "tabId": 3,
        "sequence": 1,
        "sampledAt": sampled_at,
        "videoId": "abcdefghijk",
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "url": "https://music.youtube.com/watch?v=abcdefghijk",
        "position": position,
        "duration": 200.0,
        "playbackRate": 1.0,
        "playing": playing,
        "mediaState": "playing" if playing else "paused",
        "seekedAt": 0,
        "buffering": False,
    }


class BridgeSequenceTests(unittest.TestCase):
    def test_same_millisecond_samples_are_ordered_by_sequence(self):
        state = app.BridgeState()
        first = make_track(sampled_at=1_000_000, position=10)
        first["sequence"] = 4
        second = make_track(sampled_at=1_000_000, position=11)
        second["sequence"] = 5
        delayed = make_track(sampled_at=1_000_000, position=9)
        delayed["sequence"] = 3

        state.accept(first)
        state.accept(second)
        state.accept(delayed)

        current, _ = state.snapshot()
        self.assertEqual(current["position"], 11)

    def test_pause_media_state_overrides_stale_playing_flag(self):
        payload = {
            "tabId": 1,
            "sequence": 1,
            "sampledAt": 1_000_000,
            "title": "Song",
            "artist": "Artist",
            "url": "https://music.youtube.com/watch?v=abcdefghijk",
            "position": 10,
            "duration": 100,
            "playing": True,
            "mediaState": "paused",
            "videoPaused": True,
        }
        validated = app.validate_payload(payload)
        self.assertIsNotNone(validated)
        self.assertFalse(validated["playing"])


class MediaClockMonitorTests(unittest.TestCase):
    def setUp(self):
        self.worker = app.RPCWorker(app.BridgeState(), lambda: {}, queue.Queue())

    def test_stalled_media_clock_freezes_activity_then_resumes_on_progress(self):
        samples = [
            make_track(sampled_at=1_000_000, position=20.0),
            make_track(sampled_at=1_001_000, position=20.0),
            make_track(sampled_at=1_002_000, position=20.0),
            make_track(sampled_at=1_003_000, position=20.0),
        ]
        with patch("app.time.monotonic", side_effect=[100.0, 101.0, 102.0, 104.2]):
            self.assertTrue(self.worker._effective_track(samples[0])["playing"])
            self.assertTrue(self.worker._effective_track(samples[1])["playing"])
            self.assertTrue(self.worker._effective_track(samples[2])["playing"])
            frozen = self.worker._effective_track(samples[3])
        self.assertFalse(frozen["playing"])
        self.assertEqual(frozen["mediaState"], "stalled")
        self.assertTrue(frozen["buffering"])

        resumed = make_track(sampled_at=1_004_000, position=20.6, playing=True)
        with patch("app.time.monotonic", return_value=104.5):
            live = self.worker._effective_track(resumed)
        self.assertTrue(live["playing"])
        self.assertEqual(live["mediaState"], "playing")

    def test_seek_counts_as_media_clock_progress(self):
        first = make_track(sampled_at=1_000_000, position=80.0)
        seek = make_track(sampled_at=1_001_000, position=15.0)
        seek["seekedAt"] = 1_001_000
        with patch("app.time.monotonic", side_effect=[200.0, 201.0]):
            self.worker._effective_track(first)
            after_seek = self.worker._effective_track(seek)
        self.assertTrue(after_seek["playing"])

    def test_resync_reanchors_without_clearing_existing_activity(self):
        self.worker.rpc = FakeRPC()
        cfg = {"show_button": True, "show_art": False, "show_paused": True}
        track = make_track()
        self.worker._publish_track(track, cfg)
        self.worker._publish_track(track, cfg, force_reanchor=True)
        self.assertEqual([name for name, _ in self.worker.rpc.calls], ["clear", "update", "update"])

    def test_clock_resync_interval_is_short_enough_to_limit_rate_drift(self):
        self.assertEqual(self.worker.CLOCK_RESYNC_INTERVAL, 4.5)


if __name__ == "__main__":
    unittest.main()
