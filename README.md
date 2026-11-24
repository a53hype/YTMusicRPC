# YouTube Music → Discord Rich Presence

**Updated build: 1.5.2**

A Windows desktop companion and Chrome/Edge extension that publishes the song currently playing on `music.youtube.com` to Discord Rich Presence.

## What's included

- Song title and artist, with album kept separate (used only as the album-art hover label).
- Current album/track artwork when YouTube Music exposes it.
- A Discord progress bar based on YouTube Music player API `getCurrentTime()` / `getDuration()`, with HTML media fallbacks.
- Timestamps anchored to the exact browser media sample; media seconds are never incorrectly scaled by playback speed.
- Frequent local media-clock samples and a desktop-side watchdog that freezes the activity if the media clock stops advancing while the page still reports playback.
- Serialized browser-to-desktop updates, per-tab sample sequence numbers, and same-millisecond ordering protection so delayed messages cannot restore an old track.
- Event-driven track-change, seek, pause, resume, buffering and duration-change detection, including cleanup of listeners attached to replaced media elements.
- Complete activity replacement on pause/resume/track changes. Paused activities omit `start`/`end`; new tracks provide a fresh pair. The old activity is cleared only for meaningful timeline transitions, not for periodic drift corrections.
- No rapid Rich Presence refresh spam: Discord animates the timestamp locally. The desktop app re-anchors the progress bar from the real media position every 4.5 seconds to correct accumulated drift, and immediately on seeks, track changes and play/pause transitions.
- A **Listen to Song** Discord button that opens the exact `music.youtube.com/watch?v=...` song URL. The title and album artwork are also clickable fallbacks to that same track.
- Windows system-tray operation: closing the main window hides it; the tray menu offers **Show** and **Exit**.
- Optional per-user Windows sign-in startup, configurable in the app; auto-start launches hidden directly to the system tray without a Command Prompt window.
- A hidden startup launcher so the Command Prompt does not stay open while dependencies install or the app starts.
- Local-only bridge at `127.0.0.1`; no Discord user token, bot token, cloud service or account login.

## Requirements

- Windows 10 or 11.
- Python 3.9+ with either the Python Launcher (`py`) or `python` command, plus Tcl/Tk installed. `pyw.exe` is not required.
- Discord **desktop** app running. Discord in a browser cannot receive local Rich Presence IPC.
- Chrome or Edge.

## Setup

### 1. Discord Application ID

Discord requires a registered Rich Presence Application ID. Create a Discord application at <https://discord.com/developers/applications>, name it (for example `YouTube Music`), and copy **Application ID** from General Information. No bot, bot token, OAuth scopes or redirect URI are needed. Paste the numeric ID into the desktop app and select **Save**.

### 2. Load the browser extension

1. Open `chrome://extensions` in Chrome or `edge://extensions` in Edge.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Select this project's `extension` folder (not the project root).
5. Leave the extension enabled and refresh any open YouTube Music tab.

### 3. Start the desktop companion

1. Double-click `start.bat`. It starts a hidden worker and the Command Prompt closes immediately.
2. On first run, the launcher silently installs/checks the listed Python dependencies, then starts the desktop app. It supports either the Python Launcher (`py`) or a `python` command; it does not require `pyw.exe`.
3. If Python, dependency installation, or app startup fails, Notepad opens the diagnostic log at `%LOCALAPPDATA%\YTMDiscordRPC\launcher.log` (or `%USERPROFILE%\.YTMDiscordRPC\launcher.log` if `LOCALAPPDATA` is unavailable).
4. Add your Discord Application ID and click **Save**.
5. To enable auto-start, tick **Start automatically when I sign in (minimised to tray)**. This creates a per-user Windows startup entry; it does not require administrator permission.
6. Keep Discord Desktop running and play a song on <https://music.youtube.com/>.

## Window and system tray

- Click the window's **X** or **Hide to system tray** button to keep the app running in the Windows notification area.
- To reopen it, double-click the tray icon or right-click it and select **Show YouTube Music Discord RPC**.
- To stop it completely and clear the Discord activity, right-click the tray icon and select **Exit**.
- If Windows hides notification-area icons, open the `^` hidden-icons menu to find it.
- With **Start automatically when I sign in** enabled, Windows launches the app at your next sign-in and it starts minimised to the notification area. The startup entry calls the `launch_hidden.vbs` file in the current project folder, so keep the extracted folder in a permanent location. If you move the folder, open the app once from its new location; it refreshes the startup entry automatically.

## Listen to Song button

The desktop app has a **Show Listen to Song button** option. When enabled and a valid track URL is detected, the activity payload contains a button labelled **Listen to Song** linking to the current song's exact YouTube Music watch URL. For compatibility, the song title and album image are clickable links to the same destination as well. The app log records the exact button data passed to Discord.

Discord hides Rich Presence buttons from the account that is publishing the activity. Other people viewing your profile/activity can see and click the button. To test it, ask a friend or use a second Discord account. The companion displays whether the button is ready for the current track.

## Timestamp and track-change behaviour

The extension reads the active song position and duration from YouTube Music's player API first (seconds), with the HTML media element as a fallback. It preserves the exact Unix-millisecond sample time alongside that position so an older position is never labelled as if it had just been sampled. The background worker serializes messages and the desktop bridge rejects duplicate or out-of-order samples. The desktop companion anchors Discord's start/end timestamps directly to the sampled media position. It does not divide media seconds by playback rate; the native Discord clock runs in wall-clock seconds, so the app re-anchors every 4.5 seconds to keep non-1x playback from accumulating drift.

When paused or buffering, the desktop companion replaces the full activity with a paused state and omits `start`/`end`, removing the native progress timeline. When playback resumes or a new song starts, a new activity with a fresh timestamp pair replaces the paused/old-track activity. The native Discord progress bar cannot be frozen in place; the paused activity instead shows a static `Paused · Artist · current / total` line. The publisher does not send rapid refreshes; Discord animates the timeline locally, with a 4.5-second correction update to compensate for drift.

The extension also:

- watches the media element's playback, seek, metadata, duration and buffering events, detaching listeners from replaced media elements so old events cannot corrupt the current track state;
- sends half-second media-position samples to the local bridge and explicit paused/buffering state from the actual media element;
- uses a media-clock watchdog in the desktop app: if samples continue arriving but `currentTime` stops advancing for four seconds while playback is still reported, Discord is switched to a static buffering state until real media progress resumes;
- re-anchors the native Discord progress timestamps from the actual media position every 4.5 seconds to correct drift without scaling media time by playback speed;
- polls as a fallback for YouTube Music's single-page navigation;
- identifies the current track by the player-bar YouTube video ID and waits for that ID before publishing a song or its clickable destination;
- clears the old activity immediately when the video ID changes, then waits for loaded media and stable, matching player-bar / Media Session metadata before publishing the new track;
- retries around player navigation so changes to the URL and Media Session metadata do not have to arrive in the same instant; and
- includes per-tab sequence ordering information, allowing the local bridge to ignore delayed, duplicated or out-of-order requests from before a track change.

The Discord timer is anchored on track start/resume and re-anchored after an actual seek or playback-rate/duration change. Media `currentTime` is already a song-time value, so it is never divided by playback rate. The bar animates between corrections. Pause and waiting/buffering events publish an activity without timestamps, rather than leaving a previous song timer running. The code uses `details_url` and `large_url` as clickable fallbacks in addition to the visible custom button. Discord's own Rich Presence documentation notes that custom buttons are not visible to the account publishing the activity; inspect the full profile/activity from another account. The application logs the exact button label and URL sent to the Discord RPC client in `%LOCALAPPDATA%\YTMDiscordRPC\app.log`.

## Settings and files

- **Show album artwork**: sends the current artwork URL to Discord.
- **Show Listen to Song button**: enables/disables the clickable song link.
- **Keep activity visible while paused**: retains a paused activity and shows a frozen time in its status text.
- Settings: `%LOCALAPPDATA%\YTMDiscordRPC\settings.json` (includes the auto-start preference)
- App diagnostic log: `%LOCALAPPDATA%\YTMDiscordRPC\app.log`
- Launcher/startup diagnostic log: `%LOCALAPPDATA%\YTMDiscordRPC\launcher.log`

## Privacy and security

- The extension runs only on `https://music.youtube.com/*`; the MAIN-world probe also runs only on that origin.
- A tiny MAIN-world probe reads YouTube Music's page-owned `#movie_player` API (`getVideoData`, `getVideoUrl`, `getPlayerState`, current time and duration) and passes a small snapshot to the isolated extension content script. This avoids relying on page-owned methods being visible across Chrome's isolated-world boundary.
- Track identity falls back to the player bar and page URL. Playback state and timing use the HTML media element where ready, with player API and Media Session fallbacks.
- Presence publication no longer waits for the HTML video duration to become available; a track can appear immediately while its timeline is filled in as the player reports it.
- Playback metadata is sent to `http://127.0.0.1:8765` on this PC.
- The local server binds only to loopback, checks the browser-extension origin and a bridge token, and validates song/artwork URLs.
- The desktop app communicates with the Discord desktop client's local RPC/IPC interface using the public Application ID.
- No playback data is sent to a hosted service by this project.

## Troubleshooting

**Discord does not show a presence**

- Ensure Discord Desktop is running.
- Check that the numeric Application ID was saved.
- Check that the desktop app says the Discord connection is live.

**No song is detected**

- Ensure the extension is enabled at `chrome://extensions` / `edge://extensions`.
- Refresh the YouTube Music tab after installing or reloading the extension.
- Confirm the extension is allowed to run on `music.youtube.com` (Chrome: site access should not be blocked).
- Start playback; the home page alone does not provide a current song.
- If detection still fails, open the YouTube Music tab's DevTools Console and check for `YTM Discord RPC sample failed`; also check the desktop `%LOCALAPPDATA%\YTMDiscordRPC\app.log` for bridge requests. The updated build reads the active `#movie_player` metadata first and falls back to the media element/player bar.

**The Listen to Song button is not visible to me**

- This is expected: Discord hides Rich Presence buttons from the account publishing them. Ask a friend or use a second account to inspect the activity.
- Check that **Show Listen to Song button** is enabled and the app reports the button is ready for the current track.

**The tray icon is missing**

- Check Windows' hidden notification-area icons (`^`).
- Check `%LOCALAPPDATA%\YTMDiscordRPC\launcher.log` first for Python, dependency, or startup errors, then `%LOCALAPPDATA%\YTMDiscordRPC\app.log` for runtime errors. The launcher opens its log automatically when startup fails.

**Port 8765 is already in use**

- Close another copy of the companion. Only one copy should run at a time.

## Uninstall

1. Right-click the app's tray icon and choose **Exit**.
2. Open the app one last time and untick **Start automatically when I sign in**, then click **Save**. This removes its per-user Windows startup entry.
3. Remove the extension from `chrome://extensions` / `edge://extensions`.
4. Optionally delete `%LOCALAPPDATA%\YTMDiscordRPC` to remove settings and logs.

This is an independent community utility and is not affiliated with YouTube, Google or Discord. YouTube Music may change its page/player structure, which could require future selector updates.
