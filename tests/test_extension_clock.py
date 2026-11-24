import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExtensionClockTests(unittest.TestCase):
    def test_manifest_version_and_content_script_are_present(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "1.5.2")
        self.assertTrue(any("content.js" in item["js"] for item in manifest["content_scripts"]))
        self.assertTrue(any("player_probe.js" in item["js"] and item.get("world") == "MAIN" for item in manifest["content_scripts"]))

    def test_content_script_sequences_samples_and_reports_actual_media_state(self):
        source = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
        self.assertIn("sampleSequence", source)
        self.assertIn('mediaState = "paused"', source)
        self.assertIn('mediaState = "buffering"', source)
        self.assertIn("Math.floor(track.position * 2)", source)
        self.assertIn('document.querySelector("#movie_player")', source)
        self.assertIn('player.getVideoData()', source)
        self.assertIn('player.getVideoUrl()', source)
        self.assertIn('ytm-discord-rpc-main-world', source)
        self.assertIn('player.getPlayerState()', source)
        self.assertIn('document.querySelector("video")', source)
        self.assertIn("detachObservedVideoListeners", source)
        # Detection must not be blocked while duration/readyState metadata loads.
        self.assertNotIn("mediaReady &&", source)
        self.assertNotIn("track.readyState >= 1 && track.duration > 0", source)

    def test_background_serializes_forward_requests_and_keeps_message_alive(self):
        source = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("let forwardQueue = Promise.resolve()", source)
        self.assertIn("function enqueueForward(payload)", source)
        self.assertIn("return true;", source)
        self.assertIn("AbortSignal.timeout(3000)", source)


if __name__ == "__main__":
    unittest.main()
