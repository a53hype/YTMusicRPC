from __future__ import annotations

import json
import logging
import os
import queue
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import tkinter as tk
from tkinter import messagebox, ttk

APP_NAME = "YTM Discord RPC"
APP_VERSION = "1.5.2"
HOST = "127.0.0.1"
PORT = 8765
# Shared only between this local bridge and the unpacked extension. It is not a Discord token.
BRIDGE_TOKEN = "ytm-rpc-local-bridge-2026-11-v1-7f5c2a9d"
STALE_AFTER = 14.0

APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / "YTMDiscordRPC"
CONFIG_PATH = APP_DATA / "settings.json"
LOG_PATH = APP_DATA / "app.log"
APP_DATA.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)


def load_config() -> dict[str, Any]:
    defaults = {"client_id": "", "show_button": True, "show_art": True, "show_paused": True, "start_with_windows": False}
    try:
        APP_DATA.mkdir(parents=True, exist_ok=True)
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                defaults.update(raw)
    except Exception:
        logging.exception("Could not load settings")
    return defaults


def save_config(config: dict[str, Any]) -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


STARTUP_VALUE_NAME = "YTMDiscordRPC"
STARTUP_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def windows_startup_command() -> str:
    """Return a quoted, hidden-launch command for this copy of the app."""
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    wscript = windows_dir / "System32" / "wscript.exe"
    launcher = Path(__file__).resolve().with_name("launch_hidden.vbs")
    return f'"{wscript}" //B //NoLogo "{launcher}" --start-hidden'


def set_windows_startup(enabled: bool) -> None:
    """Enable/disable per-user Windows sign-in startup (no admin rights)."""
    if os.name != "nt":
        raise OSError("Windows sign-in startup is only available on Windows.")

    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, STARTUP_RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, windows_startup_command()
            )
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass


def clean_text(value: Any, limit: int = 180) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\x00", "").split())[:limit]


def valid_song_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 1200:
        return ""
    try:
        parsed = urlparse(value)
        if parsed.scheme == "https" and parsed.hostname == "music.youtube.com" and parsed.path == "/watch":
            video_id = None
            from urllib.parse import parse_qs
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id or ""):
                return f"https://music.youtube.com/watch?v={video_id}"
    except Exception:
        pass
    return ""


def valid_art_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        return ""
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        allowed = (
            host == "googleusercontent.com"
            or host.endswith(".googleusercontent.com")
            or host == "ggpht.com"
            or host.endswith(".ggpht.com")
            or host == "ytimg.com"
            or host.endswith(".ytimg.com")
        )
        if parsed.scheme == "https" and allowed:
            return value
    except Exception:
        pass
    return ""


def validate_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    try:
        sampled_at = int(payload.get("sampledAt", time.time() * 1000))
    except (TypeError, ValueError, OverflowError):
        sampled_at = int(time.time() * 1000)
    if sampled_at < 0 or sampled_at > int(time.time() * 1000) + 60_000:
        return None

    tab_id = payload.get("tabId")
    if not isinstance(tab_id, int) or tab_id < 0:
        return None
    try:
        sequence = max(0, int(payload.get("sequence", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        sequence = 0
    session_id = clean_text(payload.get("sessionId"), 80)

    if payload.get("action") == "clear":
        return {
            "action": "clear", "tabId": tab_id, "sampledAt": sampled_at,
            "sequence": sequence, "sessionId": session_id,
        }

    title = clean_text(payload.get("title"), 180)
    artist = clean_text(payload.get("artist"), 180)
    if not title or title.lower() in {"youtube music", "music", "advertisement", "ad"}:
        return None

    try:
        position = float(payload.get("position", 0))
        duration = float(payload.get("duration", 0))
        playback_rate = float(payload.get("playbackRate", 1.0))
    except (TypeError, ValueError):
        return None
    if not (0 <= position <= 24 * 60 * 60 and 0 <= duration <= 24 * 60 * 60):
        return None
    if not (0.1 <= playback_rate <= 4.0):
        playback_rate = 1.0
    if duration > 0:
        position = min(position, duration)

    song_url = valid_song_url(payload.get("url"))
    try:
        from urllib.parse import parse_qs
        video_id = parse_qs(urlparse(song_url).query).get("v", [""])[0] if song_url else ""
    except Exception:
        video_id = ""

    try:
        seeked_at = int(payload.get("seekedAt", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        seeked_at = 0

    media_state = clean_text(payload.get("mediaState"), 24).lower()
    if media_state not in {"playing", "paused", "buffering", "ended", "stalled"}:
        media_state = "playing" if bool(payload.get("playing")) else "paused"
    # The media element's actual paused/ended flags are more reliable than a
    # stale player-button label. Honour an explicit non-playing media state.
    is_playing = bool(payload.get("playing")) and media_state == "playing"

    return {
        "action": "state",
        "tabId": tab_id,
        "sampledAt": sampled_at,
        "sequence": sequence,
        "sessionId": session_id,
        "videoId": video_id,
        "title": title,
        "artist": artist or "YouTube Music",
        "album": clean_text(payload.get("album"), 180),
        "url": song_url,
        "art": valid_art_url(payload.get("art")),
        "playing": is_playing,
        "mediaState": media_state,
        "videoPaused": bool(payload.get("videoPaused", not is_playing)),
        "videoEnded": bool(payload.get("videoEnded", False)),
        "position": position,
        "duration": duration,
        "playbackRate": playback_rate,
        "eventType": clean_text(payload.get("eventType"), 32),
        "seekedAt": max(0, seeked_at),
        "buffering": bool(payload.get("buffering")),
    }


class BridgeState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.latest: dict[str, Any] | None = None
        self.last_seen = 0.0
        self.last_sample_by_tab: dict[int, tuple[int, int]] = {}
        self.ui_events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def accept(self, payload: dict[str, Any]) -> None:
        with self.lock:
            tab_id = int(payload["tabId"])
            sampled_at = int(payload.get("sampledAt", 0))
            sequence = int(payload.get("sequence", 0) or 0)
            order = (sampled_at, sequence)
            previous_sample = self.last_sample_by_tab.get(tab_id, (-1, -1))
            # Reject stale or duplicated requests. The sequence number breaks ties
            # when multiple media events land within the same millisecond.
            if order <= previous_sample:
                return
            self.last_sample_by_tab[tab_id] = order

            if payload.get("action") == "clear":
                # A closing/background tab must not clear another tab's active song.
                if self.latest is not None and tab_id == self.latest.get("tabId"):
                    self.latest = None
                    self.last_seen = 0.0
                    self.ui_events.put(("track", None))
                return

            if self.latest is not None and tab_id == self.latest.get("tabId"):
                if sampled_at < int(self.latest.get("sampledAt", 0)):
                    return
            self.latest = payload
            self.last_seen = time.monotonic()
            self.ui_events.put(("track", payload.copy()))

    def snapshot(self) -> tuple[dict[str, Any] | None, float]:
        with self.lock:
            return (self.latest.copy() if self.latest else None, self.last_seen)


class LocalRequestHandler(BaseHTTPRequestHandler):
    server_version = "YTMDiscordBridge/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("HTTP %s - %s", self.address_string(), fmt % args)

    def _allowed_origin(self) -> str | None:
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") and len(origin) < 200:
            return origin
        return None

    def _send(self, status: int, body: bytes = b"", origin: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        origin = self._allowed_origin()
        if self.path != "/state" or not origin:
            self._send(403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-YTM-RPC-Token")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_POST(self) -> None:
        origin = self._allowed_origin()
        if self.path != "/state" or not origin:
            self._send(403)
            return
        if self.headers.get("X-YTM-RPC-Token") != BRIDGE_TOKEN:
            self._send(403, origin=origin)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                self._send(413, origin=origin)
                return
            raw = self.rfile.read(length)
            payload = validate_payload(json.loads(raw.decode("utf-8")))
            if payload is None:
                self._send(400, origin=origin)
                return
            self.server.bridge_state.accept(payload)  # type: ignore[attr-defined]
            self._send(200, b'{"ok":true}', origin=origin)
        except Exception:
            logging.exception("Failed to process browser update")
            self._send(400, origin=origin)


class BridgeHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: BridgeState):
        super().__init__(address, LocalRequestHandler)
        self.bridge_state = state


class RPCWorker(threading.Thread):
    # If media.currentTime stops advancing while the page still claims to be
    # playing, freeze the Discord timeline instead of letting it run on.
    CLOCK_STALL_AFTER = 4.0
    # Re-anchor Discord's native progress clock periodically from the browser's
    # actual media position. This is deliberately below Discord's update limit.
    CLOCK_RESYNC_INTERVAL = 4.5

    def __init__(self, state: BridgeState, get_config, ui_events: queue.Queue):
        super().__init__(name="DiscordRPC", daemon=True)
        self.state = state
        self.get_config = get_config
        self.ui_events = ui_events
        self.stop_event = threading.Event()
        self.rpc = None
        self.rpc_client_id = ""
        self.requested_client_id = ""
        self.last_connect_attempt = 0.0
        self.last_signature: tuple[Any, ...] | None = None
        self.last_clear = False
        self.last_published_track_id: str | None = None
        self.last_published_playing: bool | None = None
        self.last_published_seeked_at = 0
        self.last_published_duration: float | None = None
        self.last_published_playback_rate: float | None = None
        self.last_timeline_resync = 0.0
        self.clock_identity: str | None = None
        self.clock_last_sampled_at = 0
        self.clock_last_position: float | None = None
        self.clock_last_seeked_at = 0
        self.clock_last_progress = 0.0
        self.clock_is_stalled = False
        self.last_published_metadata: tuple[str, str] | None = None
        self.last_error = ""

    def stop(self) -> None:
        self.stop_event.set()

    def _close_rpc(self) -> None:
        if self.rpc is not None:
            try:
                self.rpc.clear()
            except Exception:
                pass
            try:
                self.rpc.close()
            except Exception:
                pass
        self.rpc = None
        self.rpc_client_id = ""
        self.last_signature = None
        self.last_clear = True
        self.last_published_track_id = None
        self.last_published_playing = None
        self.last_published_seeked_at = 0
        self.last_published_duration = None
        self.last_published_playback_rate = None
        self.last_published_metadata = None
        self.last_timeline_resync = 0.0
        self.clock_identity = None
        self.clock_last_sampled_at = 0
        self.clock_last_position = None
        self.clock_last_seeked_at = 0
        self.clock_last_progress = 0.0
        self.clock_is_stalled = False

    def _connect(self, client_id: str) -> bool:
        try:
            from pypresence import Presence
            candidate = Presence(client_id)
            candidate.connect()
            self.rpc = candidate
            self.rpc_client_id = client_id
            self.last_error = ""
            self.ui_events.put(("rpc", "Connected to Discord"))
            logging.info("Connected to Discord RPC")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.ui_events.put(("rpc", f"Discord not connected: {exc}"))
            logging.warning("Discord RPC connection failed: %s", exc)
            return False

    def _make_activity(self, track: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        from pypresence.types import ActivityType

        title = track["title"]
        artist = track["artist"]
        album = track.get("album", "")
        playing = bool(track.get("playing"))
        position = float(track.get("position", 0))
        duration = float(track.get("duration", 0))
        try:
            playback_rate = float(track.get("playbackRate", 1.0))
        except (TypeError, ValueError, OverflowError):
            playback_rate = 1.0
        if not (0.1 <= playback_rate <= 4.0):
            playback_rate = 1.0
        now_ms = time.time_ns() // 1_000_000

        # Keep the artist line clean. Album is used only as the artwork tooltip;
        # it must not be repeated after the artist in Discord's activity text.
        activity: dict[str, Any] = {
            "activity_type": ActivityType.LISTENING,
            "details": title[:128],
            "state": artist[:128],
        }

        # Pair the media position with the exact wall-clock instant at which the
        # browser sampled it. currentTime/duration are already expressed in MEDIA
        # seconds; dividing either by playbackRate changes the song timeline and
        # causes incorrect elapsed/remaining time at non-1x speeds.
        try:
            sampled_at_ms = int(track.get("sampledAt", now_ms))
        except (TypeError, ValueError, OverflowError):
            sampled_at_ms = now_ms
        if sampled_at_ms <= 0 or sampled_at_ms > now_ms + 5_000:
            sampled_at_ms = now_ms

        sample_age_seconds = max(0.0, (now_ms - sampled_at_ms) / 1000.0)
        current_position = position + sample_age_seconds * playback_rate if playing else position
        if duration > 0:
            current_position = min(current_position, duration)

        if playing and duration > 0:
            # Discord's native clock advances in wall-clock seconds. Anchor it to
            # the media player's sampled position without scaling by playbackRate.
            # Re-anchoring periodically below corrects for non-1x playback speed.
            activity["start"] = sampled_at_ms - int(position * 1000)
            activity["end"] = sampled_at_ms + int((duration - position) * 1000)
        elif not playing:
            current = self._format_time(current_position)
            total = self._format_time(duration) if duration > 0 else "--:--"
            pause_label = "Buffering" if track.get("mediaState") in {"buffering", "stalled"} else "Paused"
            activity["state"] = f"{pause_label} • {artist} • {current} / {total}"[:128]

        song_url = valid_song_url(track.get("url"))
        if song_url:
            # These clickable metadata links are a fallback if a Discord client does
            # not render the custom-button row for this activity card.
            activity["details_url"] = song_url

        if config.get("show_art", True) and track.get("art"):
            activity["large_image"] = track["art"]
            activity["large_text"] = (album or artist or "YouTube Music")[:128]
            if song_url:
                activity["large_url"] = song_url

        if config.get("show_button", True) and song_url:
            activity["buttons"] = [{"label": "Listen to Song", "url": song_url}]
        return activity

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

    @staticmethod
    def _track_identity(track: dict[str, Any]) -> str:
        return str(
            track.get("videoId")
            or track.get("url")
            or f"{track.get('title', '')}\x1f{track.get('artist', '')}"
        )

    def _effective_track(self, track: dict[str, Any] | None) -> dict[str, Any] | None:
        """Detect a media clock that has stopped despite a stale playing flag.

        YouTube Music can occasionally miss or delay a pause/waiting event during
        queue transitions. We independently watch the sampled HTMLMediaElement
        currentTime. If it stops progressing for several seconds, the activity is
        treated as paused/buffering until actual media progress resumes.
        """
        if track is None:
            self.clock_identity = None
            self.clock_last_sampled_at = 0
            self.clock_last_position = None
            self.clock_last_seeked_at = 0
            self.clock_last_progress = 0.0
            self.clock_is_stalled = False
            return None

        effective = track.copy()
        now = time.monotonic()
        identity = self._track_identity(track)
        sampled_at = int(track.get("sampledAt", 0) or 0)
        position = float(track.get("position", 0) or 0)
        seeked_at = int(track.get("seekedAt", 0) or 0)
        reported_playing = bool(track.get("playing"))

        if identity != self.clock_identity:
            self.clock_identity = identity
            self.clock_last_sampled_at = sampled_at
            self.clock_last_position = position
            self.clock_last_seeked_at = seeked_at
            self.clock_last_progress = now if reported_playing else 0.0
            self.clock_is_stalled = False
        elif sampled_at > self.clock_last_sampled_at:
            previous_position = self.clock_last_position
            seek_changed = seeked_at != self.clock_last_seeked_at
            progressed = (
                previous_position is None
                or position > previous_position + 0.04
                or (seek_changed and abs(position - previous_position) > 0.04)
            )
            if reported_playing and progressed:
                self.clock_last_progress = now
                self.clock_is_stalled = False
            elif not reported_playing:
                self.clock_last_progress = 0.0

            self.clock_last_sampled_at = sampled_at
            self.clock_last_position = position
            self.clock_last_seeked_at = seeked_at

        if reported_playing:
            if self.clock_last_progress <= 0:
                self.clock_last_progress = now
            elif now - self.clock_last_progress >= self.CLOCK_STALL_AFTER:
                effective["playing"] = False
                effective["buffering"] = True
                effective["mediaState"] = "stalled"
                if not self.clock_is_stalled:
                    self.clock_is_stalled = True
                    logging.warning(
                        "Media clock stopped advancing for %.1fs; freezing Discord timeline",
                        now - self.clock_last_progress,
                    )
        return effective

    def _needs_clock_resync(self, track: dict[str, Any] | None) -> bool:
        if not track or not track.get("playing"):
            return False
        now = time.monotonic()
        return self.last_timeline_resync <= 0 or now - self.last_timeline_resync >= self.CLOCK_RESYNC_INTERVAL

    def _presence_signature(self, track: dict[str, Any] | None, config: dict[str, Any]) -> tuple[Any, ...] | None:
        if track is None:
            return None
        playing = bool(track.get("playing"))
        return (
            self._track_identity(track),
            track.get("title"),
            track.get("artist"),
            track.get("album"),
            track.get("url"),
            track.get("art") if config.get("show_art", True) else "",
            playing,
            round(float(track.get("duration", 0) or 0), 3),
            round(float(track.get("playbackRate", 1) or 1), 3),
            # Position is intentionally excluded during normal playback. A seek gets
            # its own unique seekedAt marker, avoiding one RPC update per timeupdate.
            track.get("seekedAt", 0),
            round(float(track.get("position", 0) or 0), 2) if not playing else None,
            track.get("mediaState"),
            bool(config.get("show_button", True)),
            bool(config.get("show_paused", True)),
        )

    def _publish_track(
        self,
        track: dict[str, Any] | None,
        config: dict[str, Any],
        *,
        force_reanchor: bool = False,
    ) -> None:
        """Replace the complete Discord activity when playback state actually changes.

        SET_ACTIVITY sends the full activity. For timeline-changing transitions,
        clear first and then publish the replacement. This extra reset is deliberate:
        it prevents a stale client-side Discord timer surviving pause/resume, seeks,
        duration changes or a new song.
        """
        if self.rpc is None:
            return

        if track is None:
            if not self.last_clear:
                self.rpc.clear()
                self.ui_events.put(("rpc", "Connected to Discord · waiting for a song"))
            self.last_clear = True
            self.last_published_track_id = None
            self.last_published_playing = None
            self.last_published_seeked_at = 0
            self.last_published_duration = None
            self.last_published_playback_rate = None
            self.last_published_metadata = None
            self.last_timeline_resync = 0.0
            self.last_signature = None
            return

        playing = bool(track.get("playing"))
        track_id = self._track_identity(track)
        metadata_identity = (str(track.get("title", "")), str(track.get("artist", "")))

        if not playing and not config.get("show_paused", True):
            if not self.last_clear:
                self.rpc.clear()
            self.last_clear = True
            self.last_published_track_id = None
            self.last_published_playing = None
            self.last_published_seeked_at = 0
            self.last_published_duration = None
            self.last_published_playback_rate = None
            self.last_published_metadata = None
            self.last_timeline_resync = 0.0
            # Remember this suppressed state so the worker does not send clear()
            # repeatedly on every 100 ms loop iteration.
            self.last_signature = self._presence_signature(track, config)
            return

        try:
            duration = float(track.get("duration", 0) or 0)
            playback_rate = float(track.get("playbackRate", 1) or 1)
            seeked_at = int(track.get("seekedAt", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            duration, playback_rate, seeked_at = 0.0, 1.0, 0

        # Force Discord to discard any old timer before publishing a pause,
        # resume, new track, seek, or changed media timeline. SET_ACTIVITY is
        # normally a full replacement, but clearing first is intentionally
        # conservative because some Discord clients have retained stale timers.
        reset_timeline = (
            self.last_clear
            or self.last_published_track_id != track_id
            or self.last_published_playing != playing
            or self.last_published_seeked_at != seeked_at
            or self.last_published_duration != duration
            or self.last_published_playback_rate != playback_rate
            or (
                self.last_published_metadata is not None
                and self.last_published_metadata != metadata_identity
            )
        )
        if reset_timeline:
            try:
                self.rpc.clear()
            except Exception:
                logging.exception("Could not clear previous Discord activity before timeline reset")

        activity = self._make_activity(track, config)
        result = self.rpc.update(**activity)
        if playing and "start" in activity and "end" in activity:
            sample_age = max(0.0, (time.time_ns() // 1_000_000 - int(track.get("sampledAt", 0) or 0)) / 1000.0)
            logging.info(
                "Timeline published: video=%s position=%.3fs duration=%.3fs rate=%.3fx sample_age=%.3fs start_ms=%d end_ms=%d",
                track_id,
                float(track.get("position", 0) or 0),
                duration,
                playback_rate,
                sample_age,
                int(activity["start"]),
                int(activity["end"]),
            )
        self.last_published_track_id = track_id
        self.last_published_playing = playing
        self.last_published_seeked_at = seeked_at
        self.last_published_duration = duration
        self.last_published_playback_rate = playback_rate
        self.last_published_metadata = metadata_identity
        self.last_signature = self._presence_signature(track, config)
        self.last_clear = False
        self.last_timeline_resync = time.monotonic() if playing else 0.0

        if force_reanchor:
            logging.info("Re-anchored Discord progress from YouTube Music media position")

        if activity.get("buttons"):
            logging.info(
                "Published Rich Presence button: label=%r url=%s response=%r",
                activity["buttons"][0]["label"],
                activity["buttons"][0]["url"],
                result,
            )
            self.ui_events.put(("rpc", "Listening · Discord presence live · song button included"))
        else:
            logging.info("Published Rich Presence without a song button; no valid song URL or button disabled")
            self.ui_events.put(("rpc", "Listening · Discord presence is live"))

    def run(self) -> None:
        while not self.stop_event.is_set():
            config = self.get_config()
            client_id = str(config.get("client_id", "")).strip()

            if client_id != self.requested_client_id:
                self._close_rpc()
                self.requested_client_id = client_id
                self.last_connect_attempt = 0.0
                self.last_signature = None
                self.last_published_track_id = None
                self.last_published_playing = None
                self.last_published_seeked_at = 0
                self.last_published_duration = None
                self.last_published_playback_rate = None
                self.last_clear = False

            if client_id and self.rpc is None and time.monotonic() - self.last_connect_attempt >= 5.0:
                self.last_connect_attempt = time.monotonic()
                if re.fullmatch(r"\d{15,25}", client_id):
                    self._connect(client_id)
                else:
                    self.ui_events.put(("rpc", "Application ID should be a 15–25 digit Discord ID"))
                    self.last_connect_attempt = time.monotonic() + 3600.0

            track, seen = self.state.snapshot()
            fresh = track is not None and (time.monotonic() - seen) <= STALE_AFTER
            if not fresh:
                track = None
            else:
                track = self._effective_track(track)

            if self.rpc is not None:
                signature = self._presence_signature(track, config)
                # Publish as soon as a meaningful state change reaches the bridge.
                # During ordinary playback, Discord's timestamp bar animates itself;
                # there is no need to spam SET_ACTIVITY every few seconds.
                if signature != self.last_signature or (track is None and not self.last_clear):
                    try:
                        self._publish_track(track, config)
                    except Exception as exc:
                        logging.warning("Discord RPC update failed: %s", exc)
                        self.ui_events.put(("rpc", f"Discord RPC reconnecting: {exc}"))
                        self._close_rpc()
                elif self._needs_clock_resync(track):
                    # SET_ACTIVITY replaces the timestamps with a fresh anchor at
                    # the actual media position. A 4.5-second correction interval
                    # limits drift at non-1x playback rates without continuously
                    # publishing updates on every media timeupdate.
                    try:
                        self._publish_track(track, config, force_reanchor=True)
                    except Exception as exc:
                        logging.warning("Discord RPC clock resync failed: %s", exc)
                        self.ui_events.put(("rpc", f"Discord RPC reconnecting: {exc}"))
                        self._close_rpc()
            self.stop_event.wait(0.10)

        self._close_rpc()


class YTMDiscordApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"YouTube Music Discord RPC v{APP_VERSION}")
        self.root.geometry("560x490")
        self.root.minsize(520, 460)
        # Closing the window hides the companion in the Windows notification area.
        # The tray menu's Exit command is the explicit way to terminate it.
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.config = load_config()
        self.config_lock = threading.RLock()
        self.state = BridgeState()
        self.ui_events = self.state.ui_events
        self.server: BridgeHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.worker: RPCWorker | None = None
        self.tray_icon = None
        self.tray_thread: threading.Thread | None = None
        self.shutting_down = False
        self.current_track: dict[str, Any] | None = None

        self.client_id_var = tk.StringVar(value=str(self.config.get("client_id", "")))
        self.show_button_var = tk.BooleanVar(value=bool(self.config.get("show_button", True)))
        self.show_art_var = tk.BooleanVar(value=bool(self.config.get("show_art", True)))
        self.show_paused_var = tk.BooleanVar(value=bool(self.config.get("show_paused", True)))
        self.start_with_windows_var = tk.BooleanVar(value=bool(self.config.get("start_with_windows", False)))
        self.server_var = tk.StringVar(value="Starting local bridge…")
        self.discord_var = tk.StringVar(value="Waiting for Discord…")
        self.track_var = tk.StringVar(value="No track detected yet")
        self.artist_var = tk.StringVar(value="Open music.youtube.com and play a song")
        self.button_status_var = tk.StringVar(value="Discord button: waiting for a track link")
        self.window_status_var = tk.StringVar(value="Close this window to keep running in the system tray")

        self._build_ui()
        self._sync_windows_startup()
        self._start_tray()
        self._start_server()
        self.worker = RPCWorker(self.state, self.get_config, self.ui_events)
        self.worker.start()
        self.root.after(250, self._poll_ui_events)
        if "--start-hidden" in sys.argv[1:]:
            # Auto-start in the notification area rather than opening a window.
            self.root.after(900, self.hide_to_tray)

    def _build_ui(self) -> None:
        self.root.configure(bg="#111318")
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#111318")
        style.configure("TLabel", background="#111318", foreground="#e7e9ee", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI Semibold", 18), foreground="#ffffff")
        style.configure("Sub.TLabel", foreground="#9ba2b1", font=("Segoe UI", 9))
        style.configure("TCheckbutton", background="#111318", foreground="#e7e9ee")
        style.configure("TButton", font=("Segoe UI Semibold", 9), padding=(10, 7))

        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="YouTube Music", style="Header.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Discord Rich Presence", style="Sub.TLabel").pack(anchor="w", pady=(0, 18))

        status_card = tk.Frame(outer, bg="#1b1f27", highlightthickness=1, highlightbackground="#2b303b")
        status_card.pack(fill="x", pady=(0, 14))
        tk.Label(status_card, text="CURRENT PLAYBACK", bg="#1b1f27", fg="#929bad", font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=15, pady=(13, 6))
        tk.Label(status_card, textvariable=self.track_var, bg="#1b1f27", fg="#ffffff", font=("Segoe UI Semibold", 12), anchor="w", wraplength=480, justify="left").pack(fill="x", padx=15)
        tk.Label(status_card, textvariable=self.artist_var, bg="#1b1f27", fg="#aeb5c2", font=("Segoe UI", 9), anchor="w", wraplength=480, justify="left").pack(fill="x", padx=15, pady=(3, 12))

        ttk.Label(outer, text="Discord Application ID").pack(anchor="w")
        id_row = ttk.Frame(outer)
        id_row.pack(fill="x", pady=(5, 4))
        self.id_entry = ttk.Entry(id_row, textvariable=self.client_id_var)
        self.id_entry.pack(side="left", fill="x", expand=True, ipady=5)
        ttk.Button(id_row, text="Save", command=self.save_settings).pack(side="left", padx=(8, 0))
        ttk.Button(outer, text="Create / open Discord application", command=lambda: webbrowser.open("https://discord.com/developers/applications")).pack(anchor="w", pady=(3, 12))

        options = tk.Frame(outer, bg="#111318")
        options.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(options, text="Show album artwork", variable=self.show_art_var, command=self.save_settings).pack(anchor="w", pady=2)
        ttk.Checkbutton(options, text="Show Listen to Song button", variable=self.show_button_var, command=self.save_settings).pack(anchor="w", pady=2)
        ttk.Checkbutton(options, text="Keep activity visible while paused", variable=self.show_paused_var, command=self.save_settings).pack(anchor="w", pady=2)
        self.startup_checkbox = ttk.Checkbutton(
            options,
            text="Start automatically when I sign in (minimised to tray)",
            variable=self.start_with_windows_var,
            command=self.save_settings,
        )
        self.startup_checkbox.pack(anchor="w", pady=2)
        if os.name != "nt":
            self.startup_checkbox.configure(state="disabled")

        divider = tk.Frame(outer, bg="#2b303b", height=1)
        divider.pack(fill="x", pady=(5, 12))
        ttk.Label(outer, textvariable=self.server_var).pack(anchor="w", pady=2)
        ttk.Label(outer, textvariable=self.discord_var).pack(anchor="w", pady=2)
        ttk.Label(outer, text=f"Local bridge: http://{HOST}:{PORT}  ·  No cloud connection", style="Sub.TLabel").pack(anchor="w", pady=(11, 0))
        ttk.Label(outer, textvariable=self.button_status_var, style="Sub.TLabel", wraplength=500).pack(anchor="w", pady=(5, 0))
        ttk.Label(outer, textvariable=self.window_status_var, style="Sub.TLabel", wraplength=500).pack(anchor="w", pady=(3, 0))
        ttk.Label(outer, text="The browser extension is installed separately from the extension folder.", style="Sub.TLabel", wraplength=500).pack(anchor="w", pady=(3, 0))
        ttk.Button(outer, text="Hide to system tray", command=self.hide_to_tray).pack(anchor="e", pady=(10, 0))

    def get_config(self) -> dict[str, Any]:
        with self.config_lock:
            return dict(self.config)

    def save_settings(self) -> None:
        new_id = self.client_id_var.get().strip()
        if new_id and not re.fullmatch(r"\d{15,25}", new_id):
            messagebox.showerror("Invalid Application ID", "Paste the numeric Application ID from Discord Developer Portal → General Information.")
            return
        requested_startup = self.start_with_windows_var.get()
        try:
            if os.name == "nt":
                set_windows_startup(requested_startup)
        except Exception as exc:
            logging.exception("Could not update Windows sign-in startup")
            self.start_with_windows_var.set(bool(self.config.get("start_with_windows", False)))
            messagebox.showerror(
                "Could not change automatic startup",
                f"Windows could not update the per-user startup entry.\n\n{exc}",
            )
            return

        with self.config_lock:
            self.config.update({
                "client_id": new_id,
                "show_button": self.show_button_var.get(),
                "show_art": self.show_art_var.get(),
                "show_paused": self.show_paused_var.get(),
                "start_with_windows": requested_startup,
            })
            save_config(self.config)
        self.discord_var.set("Settings saved · connecting to Discord…" if new_id else "Settings saved · add your Discord Application ID")
        self._refresh_button_status(self.current_track)

    def _sync_windows_startup(self) -> None:
        """Keep the Run entry in sync with the saved preference and current folder."""
        if os.name != "nt":
            return
        try:
            set_windows_startup(bool(self.config.get("start_with_windows", False)))
        except Exception:
            logging.exception("Could not synchronise Windows sign-in startup")
            self.window_status_var.set(
                "Could not configure Windows auto-start; check app.log for details"
            )

    def _start_server(self) -> None:
        try:
            self.server = BridgeHTTPServer((HOST, PORT), self.state)
            self.server_thread = threading.Thread(target=self.server.serve_forever, name="LocalBridge", daemon=True)
            self.server_thread.start()
            self.server_var.set(f"Local bridge running on {HOST}:{PORT}")
            logging.info("Local bridge listening on %s:%s", HOST, PORT)
        except OSError as exc:
            self.server_var.set(f"Could not start local bridge: {exc}")
            logging.exception("Could not start local HTTP server")

    def _poll_ui_events(self) -> None:
        try:
            while True:
                event, value = self.ui_events.get_nowait()
                if event == "track":
                    self.current_track = value.copy() if value else None
                    if value:
                        self.track_var.set(value.get("title", "Unknown track"))
                        self.artist_var.set(value.get("artist", ""))
                        if self.tray_icon:
                            try:
                                self.tray_icon.title = f"YouTube Music · {value.get('title', 'Playing')[:48]}"
                            except Exception:
                                pass
                    else:
                        self.track_var.set("No track detected yet")
                        self.artist_var.set("Open music.youtube.com and play a song")
                        if self.tray_icon:
                            try:
                                self.tray_icon.title = APP_NAME
                            except Exception:
                                pass
                    self._refresh_button_status(self.current_track)
                elif event == "rpc":
                    self.discord_var.set(str(value))
                elif event == "tray-show":
                    self._show_window()
                elif event == "tray-exit":
                    self.shutdown()
        except queue.Empty:
            pass
        if not self.shutting_down:
            try:
                if self.root.winfo_exists():
                    self.root.after(250, self._poll_ui_events)
            except tk.TclError:
                pass

    def _refresh_button_status(self, track: dict[str, Any] | None) -> None:
        if not self.show_button_var.get():
            self.button_status_var.set("Discord Listen to Song button is disabled in settings")
        elif track and track.get("url"):
            self.button_status_var.set(
                'Song URL detected · Listen to Song button is included in the next Discord activity update (visible to other users)'
            )
        else:
            self.button_status_var.set("Discord button: waiting for YouTube Music to provide this track's link")

    def _start_tray(self) -> None:
        """Create the Windows notification-area icon and its Show / Exit menu."""
        try:
            import pystray
            from PIL import Image, ImageDraw

            image = Image.new("RGBA", (64, 64), (17, 19, 24, 255))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(27, 31, 39, 255), outline=(88, 101, 242, 255), width=3)
            # A simple music-note glyph, drawn locally so no external icon file is needed.
            accent = (235, 238, 255, 255)
            draw.line((39, 12, 39, 43), fill=accent, width=5)
            draw.line((39, 12, 53, 16), fill=accent, width=5)
            draw.ellipse((23, 36, 40, 52), fill=accent)

            menu = pystray.Menu(
                pystray.MenuItem("Show YouTube Music Discord RPC", self._tray_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self._tray_exit),
            )
            self.tray_icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
            self.tray_thread = threading.Thread(
                target=self.tray_icon.run,
                name="SystemTray",
                daemon=True,
            )
            self.tray_thread.start()
            logging.info("System tray icon started")
        except Exception as exc:
            self.tray_icon = None
            logging.exception("Could not start system tray icon")
            self.window_status_var.set(f"System tray unavailable: {exc}")

    def _tray_show(self, _icon=None, _item=None) -> None:
        # pystray callbacks run on a background thread; hand UI work to Tk's queue.
        self.ui_events.put(("tray-show", None))

    def _tray_exit(self, _icon=None, _item=None) -> None:
        self.ui_events.put(("tray-exit", None))

    def _show_window(self) -> None:
        if self.shutting_down:
            return
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        self.window_status_var.set("Running normally · close this window to hide it in the system tray")

    def hide_to_tray(self) -> None:
        if self.shutting_down:
            return
        if self.tray_icon is None:
            # Do not make the app disappear if tray creation failed.
            self.window_status_var.set("Could not create a tray icon; leave this window open")
            return
        self.root.withdraw()

    def shutdown(self) -> None:
        """Fully exit the companion and clear its Discord activity."""
        if self.shutting_down:
            return
        self.shutting_down = True
        logging.info("Application shutdown requested")

        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                logging.exception("Could not stop system tray icon")

        if self.worker:
            self.worker.stop()
            self.worker.join(timeout=3.0)
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                logging.exception("Could not stop local bridge")
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main() -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    YTMDiscordApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
