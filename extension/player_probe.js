(() => {
  // Runs in the YouTube Music page's MAIN world so the page-owned #movie_player
  // API remains accessible even when Chrome's isolated content-script world
  // cannot see methods attached by YouTube's own scripts.
  if (window.__ytmDiscordRpcPlayerProbeLoaded) return;
  window.__ytmDiscordRpcPlayerProbeLoaded = true;

  const SOURCE = "ytm-discord-rpc-main-world";
  const SAMPLE_MS = 250;

  function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? number : 0;
  }

  function safeCall(target, method, fallback) {
    try {
      return typeof target?.[method] === "function" ? target[method]() : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function publishSnapshot() {
    try {
      if (location.hostname !== "music.youtube.com") return;
      const player = document.querySelector("#movie_player");
      const data = safeCall(player, "getVideoData", {}) || {};
      const playerState = Number(safeCall(player, "getPlayerState", -1));
      const playerUrl = String(safeCall(player, "getVideoUrl", "") || "");
      const progress = safeCall(player, "getProgressState", {}) || {};
      const video = document.querySelector("#movie_player video") ||
        document.querySelector("video.html5-main-video") ||
        document.querySelector("video");

      const videoReady = Boolean(video && video.readyState >= 1);
      const apiCurrentRaw = safeCall(player, "getCurrentTime", progress.current ?? progress.currentTime ?? null);
      const apiCurrent = apiCurrentRaw === null || apiCurrentRaw === undefined ? NaN : Number(apiCurrentRaw);
      const hasApiCurrent = Number.isFinite(apiCurrent) && apiCurrent >= 0;
      const apiDurationRaw = safeCall(player, "getDuration", progress.duration ?? null);
      const apiDuration = apiDurationRaw === null || apiDurationRaw === undefined ? NaN : Number(apiDurationRaw);
      const hasApiDuration = Number.isFinite(apiDuration) && apiDuration > 0;
      // YouTube's player API returns elapsed media time in seconds. Prefer that
      // canonical clock and fall back to HTMLMediaElement only when unavailable.
      const currentTime = hasApiCurrent ? apiCurrent : finite(video?.currentTime);
      const duration = hasApiDuration ? apiDuration : finite(video?.duration);
      const positionSource = hasApiCurrent ? "youtube-player-api" : "html-media";

      const snapshot = {
        videoId: String(data.video_id || data.videoId || ""),
        title: String(data.title || ""),
        artist: String(data.author || ""),
        url: playerUrl,
        playerState: Number.isFinite(playerState) ? playerState : -1,
        currentTime,
        duration,
        positionSource,
        playbackRate: finite(video?.playbackRate) || 1,
        readyState: Number(video?.readyState || 0),
        hasVideo: Boolean(video),
        videoReady,
        videoPaused: videoReady ? Boolean(video.paused) : playerState !== 1,
        videoEnded: videoReady ? Boolean(video.ended) : playerState === 0,
        currentSrc: String(video?.currentSrc || ""),
        sampledAt: Date.now()
      };

      // Only primitive player metadata and timing are forwarded. The isolated
      // extension content script then sends it to the loopback desktop bridge.
      window.postMessage({ source: SOURCE, type: "player-snapshot", payload: snapshot }, location.origin);
    } catch (_) {}
  }

  publishSnapshot();
  window.setInterval(publishSnapshot, SAMPLE_MS);
  ["yt-navigate-finish", "yt-page-data-updated", "ytmusic-player-state-changed", "videodatachange"].forEach((name) => {
    document.addEventListener(name, publishSnapshot, true);
  });
})();
