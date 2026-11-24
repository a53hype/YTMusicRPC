import sys
import types
import unittest
from unittest.mock import patch

# The activity builder only needs this enum from pypresence; provide a local stub
# so these tests can run before the Windows dependencies are installed.
pypresence = types.ModuleType("pypresence")
pypresence_types = types.ModuleType("pypresence.types")
pypresence_types.ActivityType = types.SimpleNamespace(LISTENING=2)
sys.modules.setdefault("pypresence", pypresence)
sys.modules.setdefault("pypresence.types", pypresence_types)

import app


class BridgeOrderingTests(unittest.TestCase):
    def test_old_sample_does_not_replace_new_track(self):
        state = app.BridgeState()
        state.accept({"action": "state", "tabId": 7, "sampledAt": 200, "title": "New"})
        state.accept({"action": "state", "tabId": 7, "sampledAt": 100, "title": "Old"})
        track, _ = state.snapshot()
        self.assertEqual(track["title"], "New")

    def test_old_clear_does_not_clear_new_track(self):
        state = app.BridgeState()
        state.accept({"action": "state", "tabId": 7, "sampledAt": 200, "title": "New"})
        state.accept({"action": "clear", "tabId": 7, "sampledAt": 100})
        track, _ = state.snapshot()
        self.assertEqual(track["title"], "New")

    def test_other_tab_clear_does_not_clear_current_track(self):
        state = app.BridgeState()
        state.accept({"action": "state", "tabId": 7, "sampledAt": 200, "title": "Current"})
        state.accept({"action": "clear", "tabId": 8, "sampledAt": 300})
        track, _ = state.snapshot()
        self.assertEqual(track["title"], "Current")


class ActivityTests(unittest.TestCase):
    def test_album_is_not_appended_to_artist_and_button_is_present(self):
        worker = app.RPCWorker(app.BridgeState(), lambda: {}, app.queue.Queue())
        with patch("app.time.time_ns", return_value=1_000_000_000_000_000):
            activity = worker._make_activity({
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "playing": True,
                "position": 20.0,
                "duration": 200.0,
                "sampledAt": 1_000_000_000_000,
                "url": "https://music.youtube.com/watch?v=abcdefghijk",
                "art": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg",
            }, {"show_art": True, "show_button": True})
        self.assertEqual(activity["state"], "Artist")
        self.assertEqual(activity["large_text"], "Album")
        self.assertEqual(activity["buttons"][0]["label"], "Listen to Song")
        self.assertEqual(activity["buttons"][0]["url"], "https://music.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(activity["details_url"], "https://music.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(activity["large_url"], "https://music.youtube.com/watch?v=abcdefghijk")

    def test_timestamp_advances_by_sample_age(self):
        worker = app.RPCWorker(app.BridgeState(), lambda: {}, app.queue.Queue())
        now_ms = 1_000_000_000_000
        with patch("app.time.time_ns", return_value=now_ms * 1_000_000):
            activity = worker._make_activity({
                "title": "Song", "artist": "Artist", "playing": True,
                "position": 30.0, "duration": 180.0,
                "sampledAt": now_ms - 2_000,
            }, {"show_art": False, "show_button": False})
        self.assertEqual(activity["start"], now_ms - 32_000)
        self.assertEqual(activity["end"], now_ms + 148_000)


    def test_media_timestamps_are_not_scaled_by_playback_rate(self):
        worker = app.RPCWorker(app.BridgeState(), lambda: {}, app.queue.Queue())
        now_ms = 1_000_000_000_000
        track = {
            "title": "Slow Song", "artist": "Artist", "playing": True,
            "position": 30.0, "duration": 180.0,
            "playbackRate": 0.75, "sampledAt": now_ms,
        }
        with patch("app.time.time_ns", return_value=now_ms * 1_000_000):
            activity = worker._make_activity(track, {"show_art": False, "show_button": False})
        # A 30-second media position must remain 30 seconds, even at 0.75x.
        self.assertEqual(activity["start"], now_ms - 30_000)
        self.assertEqual(activity["end"], now_ms + 150_000)

    def test_fast_playback_does_not_shorten_media_timestamps(self):
        worker = app.RPCWorker(app.BridgeState(), lambda: {}, app.queue.Queue())
        now_ms = 1_000_000_000_000
        track = {
            "title": "Fast Song", "artist": "Artist", "playing": True,
            "position": 45.0, "duration": 210.0,
            "playbackRate": 1.5, "sampledAt": now_ms,
        }
        with patch("app.time.time_ns", return_value=now_ms * 1_000_000):
            activity = worker._make_activity(track, {"show_art": False, "show_button": False})
        self.assertEqual(activity["start"], now_ms - 45_000)
        self.assertEqual(activity["end"], now_ms + 165_000)



class FakeRPC:
    def __init__(self):
        self.calls = []

    def clear(self):
        self.calls.append(("clear", {}))

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))


class PresenceTransitionTests(unittest.TestCase):
    def setUp(self):
        self.state = app.BridgeState()
        self.worker = app.RPCWorker(self.state, lambda: {}, app.queue.Queue())
        self.worker.rpc = FakeRPC()
        self.config = {"show_art": False, "show_button": True, "show_paused": True}

    def track(self, video_id="abcdefghijk", playing=True, position=20.0):
        return {
            "videoId": video_id,
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "playing": playing,
            "position": position,
            "duration": 200.0,
            "playbackRate": 1.0,
            "sampledAt": 1_000_000_000_000,
            "url": f"https://music.youtube.com/watch?v={video_id}",
        }

    def test_pause_replaces_activity_without_a_running_timeline(self):
        self.worker.last_published_track_id = "abcdefghijk"
        self.worker.last_published_playing = True
        self.worker.last_published_seeked_at = 0
        self.worker.last_published_duration = 200.0
        self.worker.last_published_playback_rate = 1.0
        self.worker.last_clear = False
        self.worker._publish_track(self.track(playing=False, position=42.0), self.config)

        self.assertEqual([call[0] for call in self.worker.rpc.calls], ["clear", "update"])
        activity = self.worker.rpc.calls[1][1]
        self.assertNotIn("start", activity)
        self.assertNotIn("end", activity)
        self.assertIn("Paused", activity["state"])

    def test_new_track_replaces_previous_timeline_directly(self):
        self.worker.last_published_track_id = "oldvideo123"
        self.worker.last_published_playing = True
        self.worker.last_published_seeked_at = 0
        self.worker.last_published_duration = 200.0
        self.worker.last_published_playback_rate = 1.0
        self.worker.last_clear = False
        self.worker._publish_track(self.track(video_id="newvideo123"), self.config)
        self.assertEqual([call[0] for call in self.worker.rpc.calls], ["clear", "update"])
        self.assertIn("start", self.worker.rpc.calls[1][1])
        self.assertIn("end", self.worker.rpc.calls[1][1])

    def test_repeated_paused_state_is_stable(self):
        self.worker.last_published_track_id = "abcdefghijk"
        self.worker.last_published_playing = False
        self.worker.last_published_seeked_at = 0
        self.worker.last_published_duration = 200.0
        self.worker.last_published_playback_rate = 1.0
        self.worker.last_clear = False
        self.worker._publish_track(self.track(playing=False), self.config)
        self.assertEqual([call[0] for call in self.worker.rpc.calls], ["update"])

    def test_resume_replaces_paused_activity_with_timeline(self):
        self.worker.last_published_track_id = "abcdefghijk"
        self.worker.last_published_playing = False
        self.worker.last_published_seeked_at = 0
        self.worker.last_published_duration = 200.0
        self.worker.last_published_playback_rate = 1.0
        self.worker.last_clear = False
        self.worker._publish_track(self.track(playing=True), self.config)
        self.assertEqual([call[0] for call in self.worker.rpc.calls], ["clear", "update"])
        self.assertIn("start", self.worker.rpc.calls[1][1])
        self.assertIn("end", self.worker.rpc.calls[1][1])

    def test_seek_clears_previous_timeline_before_reanchoring(self):
        self.worker.last_published_track_id = "abcdefghijk"
        self.worker.last_published_playing = True
        self.worker.last_published_seeked_at = 0
        self.worker.last_published_duration = 200.0
        self.worker.last_published_playback_rate = 1.0
        self.worker.last_clear = False
        seeked = self.track(position=85.0)
        seeked["seekedAt"] = 1_000_000_000_222
        self.worker._publish_track(seeked, self.config)
        self.assertEqual([call[0] for call in self.worker.rpc.calls], ["clear", "update"])
        self.assertIn("start", self.worker.rpc.calls[1][1])

    def test_playing_signature_ignores_normal_position_ticks_but_tracks_seeks(self):
        first = self.track(position=10.0)
        next_tick = self.track(position=11.0)
        next_tick["sampledAt"] += 1000
        self.assertEqual(
            self.worker._presence_signature(first, self.config),
            self.worker._presence_signature(next_tick, self.config),
        )
        seek = self.track(position=75.0)
        seek["seekedAt"] = 1_000_000_000_123
        self.assertNotEqual(
            self.worker._presence_signature(first, self.config),
            self.worker._presence_signature(seek, self.config),
        )

    def test_hidden_paused_activity_is_cleared_only_once(self):
        config = {**self.config, "show_paused": False}
        paused = self.track(playing=False)
        self.worker._publish_track(paused, config)
        self.worker._publish_track(paused, config)
        self.assertEqual([call[0] for call in self.worker.rpc.calls], ["clear"])

    def test_track_timestamp_uses_media_seconds_not_playback_rate(self):
        now_ms = 1_000_000_000_000
        track = self.track(position=30.0)
        track["duration"] = 180.0
        track["playbackRate"] = 2.0
        with patch("app.time.time_ns", return_value=now_ms * 1_000_000):
            activity = self.worker._make_activity(track, self.config)
        self.assertEqual(activity["start"], now_ms - 30_000)
        self.assertEqual(activity["end"], now_ms + 150_000)

    def test_payload_validation_preserves_song_video_id_and_playback_rate(self):
        payload = app.validate_payload({
            "tabId": 1,
            "sampledAt": 1_000_000_000_000,
            "title": "Song",
            "artist": "Artist",
            "url": "https://music.youtube.com/watch?v=abcdefghijk",
            "position": 12,
            "duration": 200,
            "playbackRate": 1.5,
            "playing": True,
        })
        self.assertEqual(payload["videoId"], "abcdefghijk")
        self.assertEqual(payload["playbackRate"], 1.5)
        self.assertEqual(payload["eventType"], "")
        self.assertEqual(payload["seekedAt"], 0)
        payload_with_seek = app.validate_payload({
            "tabId": 1,
            "sampledAt": 1_000_000_000_000,
            "title": "Song",
            "artist": "Artist",
            "url": "https://music.youtube.com/watch?v=abcdefghijk",
            "position": 20,
            "duration": 200,
            "playing": True,
            "eventType": "seeked",
            "seekedAt": 1_000_000_000_111,
            "buffering": False,
        })
        self.assertEqual(payload_with_seek["eventType"], "seeked")
        self.assertEqual(payload_with_seek["seekedAt"], 1_000_000_000_111)

if __name__ == "__main__":
    unittest.main()
