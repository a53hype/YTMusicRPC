(() => {
  if (window.__ytmDiscordRpcLoaded) return;
  window.__ytmDiscordRpcLoaded = true;

  // The player-bar link describes the track the user is actually hearing. On a
  // YouTube Music SPA transition, location.href can change a little earlier than
  // the player bar and media element, so it is deliberately only a fallback.
  const POLL_MS = 200;
  // Send media-clock samples often enough to notice a stalled/paused media
  // element, but keep the higher-frequency traffic on the local bridge only.
  const HEARTBEAT_MS = 1000;
  const STABLE_SAMPLES_REQUIRED = 3;
  const STABLE_FOR_MS = 500;
  const NO_METADATA_CLEAR_MS = 1200;

  let lastFingerprint = "";
  let lastSentAt = 0;
  let lastPublishedTrackKey = "";
  let lastPublishedMetadataKey = "";
  let lastPublishedVideoId = "";
  let wasActive = false;
  let observedVideo = null;
  let detachObservedVideoListeners = null;
  let pendingCandidate = null;
  let noMetadataSince = 0;
  let lastSeekedAt = 0;
  let mediaWaiting = false;
  let sampleSequence = 0;
  let latestMainPlayerSnapshot = null;
  const sourceSessionId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

  function sendBridgeMessage(message) {
    try {
      chrome.runtime.sendMessage({
        ...message,
        sessionId: sourceSessionId,
        sequence: ++sampleSequence
      });
    } catch (_) {}
  }

  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const normalise = (value) => clean(value).toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

  function textFrom(selectors) {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const value = clean(node?.textContent);
      if (value) return value;
    }
    return "";
  }

  function getPlayerApi() {
    // YouTube Music exposes its active player through #movie_player. These
    // methods return the actual queue item, unlike location.href which can be
    // updated ahead of the media element during SPA transitions.
    return document.querySelector("#movie_player") ||
      document.querySelector("ytmusic-player #movie_player") ||
      null;
  }

  function getPlayerSnapshot() {
    // Prefer the MAIN-world probe's fresh snapshot. It can call page-owned
    // YouTube Music methods even when the isolated world cannot see them.
    if (latestMainPlayerSnapshot && Date.now() - latestMainPlayerSnapshot.sampledAt < 1000) {
      const main = latestMainPlayerSnapshot;
      return {
        player: null,
        data: { video_id: main.videoId, title: main.title, author: main.artist },
        playerState: Number(main.playerState ?? -1),
        url: String(main.url || ""),
        progress: { current: main.currentTime, duration: main.duration },
        sampledAt: main.sampledAt
      };
    }
    const player = getPlayerApi();
    const snapshot = { player, data: {}, playerState: -1, url: "", progress: {} };
    if (!player) return snapshot;
    try {
      if (typeof player.getVideoData === "function") {
        snapshot.data = player.getVideoData() || {};
      }
    } catch (_) {}
    try {
      if (typeof player.getPlayerState === "function") {
        snapshot.playerState = Number(player.getPlayerState());
      }
    } catch (_) {}
    try {
      if (typeof player.getVideoUrl === "function") {
        snapshot.url = String(player.getVideoUrl() || "");
      }
    } catch (_) {}
    try {
      if (typeof player.getProgressState === "function") {
        snapshot.progress = player.getProgressState() || {};
      }
    } catch (_) {}
    return snapshot;
  }

  function getVideo() {
    // Keep the specific player selectors first, then fall back to the actual
    // HTMLMediaElement. Recent YouTube Music builds have changed wrapper
    // structure while retaining a regular <video> element.
    return document.querySelector("ytmusic-player video.html5-main-video") ||
      document.querySelector("#movie_player video") ||
      document.querySelector("video.html5-main-video") ||
      document.querySelector("ytmusic-player video") ||
      document.querySelector("video") ||
      null;
  }

  function getMediaMetadata() {
    try {
      const metadata = navigator.mediaSession?.metadata;
      if (!metadata) return {};
      const artwork = Array.from(metadata.artwork || []);
      artwork.sort((a, b) => {
        const size = (item) => {
          const match = String(item.sizes || "").match(/(\d+)x(\d+)/);
          return match ? Number(match[1]) * Number(match[2]) : 0;
        };
        return size(b) - size(a);
      });
      return {
        title: clean(metadata.title),
        artist: clean(metadata.artist),
        album: clean(metadata.album),
        art: artwork[0]?.src || ""
      };
    } catch (_) {
      return {};
    }
  }

  function idFromHref(href) {
    if (!href) return "";
    try {
      const id = new URL(href, location.origin).searchParams.get("v");
      return id && /^[A-Za-z0-9_-]{6,20}$/.test(id) ? id : "";
    } catch (_) {
      return "";
    }
  }

  function parseVideoId(playerSnapshot) {
    // Prefer the ID reported by the active player API. Its video data follows
    // the queue item actually loaded, even when the address bar is mid-transition.
    const apiId = String(playerSnapshot?.data?.video_id || playerSnapshot?.data?.videoId || "");
    if (/^[A-Za-z0-9_-]{6,20}$/.test(apiId)) return { id: apiId, source: "player-api" };

    const apiUrlId = idFromHref(playerSnapshot?.url);
    if (apiUrlId) return { id: apiUrlId, source: "player-api-url" };

    // The current player-bar link is the next best source when the API is not
    // exposed in the extension's isolated world.
    const selectors = [
      "ytmusic-player-bar .content-info-wrapper a[href*='/watch?v=']",
      "ytmusic-player-bar #title a[href*='/watch?v=']",
      "ytmusic-player-bar a[href*='/watch?v=']"
    ];
    for (const selector of selectors) {
      const anchor = document.querySelector(selector);
      const id = idFromHref(anchor?.href || anchor?.getAttribute?.("href"));
      if (id) return { id, source: "player-bar" };
    }

    try {
      const current = new URL(location.href);
      const id = current.searchParams.get("v");
      if (id && /^[A-Za-z0-9_-]{6,20}$/.test(id)) return { id, source: "page-url" };
    } catch (_) {}
    return { id: "", source: "none" };
  }

  function finiteNonNegative(value) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? number : 0;
  }

  function readPlayerTime(snapshot, methodName, progressNames) {
    try {
      if (typeof snapshot.player?.[methodName] === "function") {
        const value = Number(snapshot.player[methodName]());
        if (Number.isFinite(value) && value >= 0) return value;
      }
    } catch (_) {}
    for (const name of progressNames) {
      const value = Number(snapshot.progress?.[name]);
      if (Number.isFinite(value) && value >= 0) return value;
    }
    return 0;
  }

  function parseByline(value) {
    const pieces = clean(value).split(/\s*[•·]\s*/).filter(Boolean);
    return {
      artist: pieces[0] || clean(value),
      album: pieces.length > 1 ? pieces.slice(1).join(" • ") : ""
    };
  }

  function removeAlbumFromArtist(artist, album) {
    if (!artist || !album) return artist;
    const parts = artist.split(/\s*[•·]\s*/).filter(Boolean);
    const remaining = parts.filter((part) => part.toLocaleLowerCase() !== album.toLocaleLowerCase());
    return remaining.length ? remaining.join(" • ") : artist;
  }

  function getTrack() {
    const player = getPlayerSnapshot();
    const media = getMediaMetadata();
    const domTitle = textFrom([
      "ytmusic-player-bar .title.ytmusic-player-bar",
      "ytmusic-player-bar #title",
      "ytmusic-player-bar .title",
      "ytmusic-player-bar yt-formatted-string.title"
    ]);
    const domByline = textFrom([
      "ytmusic-player-bar .byline.ytmusic-player-bar",
      "ytmusic-player-bar #subtitle",
      "ytmusic-player-bar .subtitle",
      "ytmusic-player-bar yt-formatted-string.subtitle"
    ]);
    const byline = parseByline(domByline);

    // Player API metadata and ID belong to the same active queue item. Prefer
    // them when available so title/artist cannot be paired with a stale route.
    const apiTitle = clean(player.data?.title);
    const apiArtist = clean(player.data?.author);
    const title = apiTitle || domTitle || media.title || "";
    let artist = apiArtist || byline.artist || media.artist || "";
    const album = byline.album || media.album || "";
    artist = removeAlbumFromArtist(artist, album) || "YouTube Music";

    const video = getVideo();
    const apiPosition = readPlayerTime(player, "getCurrentTime", ["current", "currentTime"]);
    const apiDuration = readPlayerTime(player, "getDuration", ["duration"]);
    const videoPosition = video && Number.isFinite(video.currentTime) ? finiteNonNegative(video.currentTime) : 0;
    const videoDuration = video && Number.isFinite(video.duration) ? finiteNonNegative(video.duration) : 0;
    const videoTimelineReady = Boolean(video && video.readyState >= 1);
    // When the MAIN-world probe supplied the position, keep its sample timestamp
    // attached to that exact value. Do not combine an older position with a newer
    // Date.now() timestamp, as that makes Discord think the song is further ahead.
    const hasMainWorldSample = Number.isFinite(Number(player.sampledAt)) && Number(player.sampledAt) > 0;
    const position = hasMainWorldSample ? apiPosition : (videoTimelineReady ? videoPosition : apiPosition || videoPosition);
    const duration = hasMainWorldSample ? apiDuration : (videoDuration > 0 ? videoDuration : apiDuration);
    const positionSampledAt = hasMainWorldSample ? Number(player.sampledAt) : Date.now();

    let mediaPlaybackState = "";
    try {
      mediaPlaybackState = navigator.mediaSession?.playbackState || "";
    } catch (_) {}

    let playing;
    let mediaState;
    if (videoTimelineReady) {
      if (video.ended) mediaState = "ended";
      else if (video.paused) mediaState = "paused";
      else if (mediaWaiting) mediaState = "buffering";
      else mediaState = "playing";
      playing = mediaState === "playing";
    } else if (player.playerState === 3 || (mediaWaiting && player.playerState === 1)) {
      // YouTube player state 3 means buffering.
      playing = false;
      mediaState = "buffering";
    } else if (player.playerState === 1 || player.playerState === 2 || player.playerState === 5) {
      // IFrame/HTML5 player state: 1 playing, 2 paused, 5 cued. Prefer this
      // during metadata loading, when the HTMLMediaElement may still be stale.
      playing = player.playerState === 1 && !mediaWaiting;
      mediaState = mediaWaiting && player.playerState === 1 ? "buffering" : (playing ? "playing" : "paused");
    } else if (video) {
      if (video.ended) mediaState = "ended";
      else if (video.paused) mediaState = "paused";
      else if (mediaWaiting) mediaState = "buffering";
      else mediaState = "playing";
      playing = mediaState === "playing";
    } else {
      playing = mediaPlaybackState === "playing";
      mediaState = playing ? (mediaWaiting ? "buffering" : "playing") : "paused";
    }

    const videoReference = parseVideoId(player);
    const videoId = videoReference.id;
    const url = videoId ? `https://music.youtube.com/watch?v=${videoId}` : "";
    const art = media.art ||
      document.querySelector("ytmusic-player-bar #song-image img, ytmusic-player-bar #thumbnail img, ytmusic-player-bar img")?.src ||
      "";

    // The player API gives a coherent metadata+video ID pair. When it is not
    // available, don't make a disagreement with Media Session a hard blocker:
    // YouTube Music can update these sources in different animation frames.
    const metadataConsistent = Boolean(player.data?.video_id || player.data?.videoId) ||
      !domTitle || !media.title || normalise(domTitle) === normalise(media.title);

    return {
      videoId,
      title,
      artist,
      album,
      url,
      art,
      playing,
      mediaState,
      videoPaused: videoTimelineReady ? Boolean(video.paused) : !playing,
      videoEnded: video ? Boolean(video.ended) : player.playerState === 0,
      position,
      duration,
      sampledAt: positionSampledAt,
      positionSource: hasMainWorldSample ? "youtube-player-api" : (videoTimelineReady ? "html-media" : "player-fallback"),
      playbackRate: video && Number.isFinite(video.playbackRate) ? video.playbackRate : 1,
      readyState: video?.readyState ?? 0,
      mediaSource: video?.currentSrc || "",
      videoIdSource: videoReference.source,
      playerState: player.playerState,
      buffering: mediaWaiting,
      metadataConsistent,
      hasVideo: Boolean(video)
    };
  }

  function sendClear(resetTrack = true) {
    if (wasActive || pendingCandidate) {
      sendBridgeMessage({ type: "ytm-rpc-clear", sampledAt: Date.now() });
    }
    wasActive = false;
    lastFingerprint = "";
    if (resetTrack) {
      lastPublishedTrackKey = "";
      lastPublishedMetadataKey = "";
      lastPublishedVideoId = "";
      pendingCandidate = null;
      noMetadataSince = 0;
    }
  }

  function candidateKey(track) {
    // Include visible metadata as well as the video ID. Some queue/playlist
    // transitions reuse the SPA route while the current player-bar track changes.
    return [track.videoId, normalise(track.title), normalise(track.artist)].join("\u001f");
  }

  function sampleAndSend(force = false, eventType = "poll") {
    try {
      if (location.hostname !== "music.youtube.com") {
        sendClear();
        return;
      }

      const track = getTrack();
      // Preserve the timestamp created by the MAIN-world probe when its position
      // is used. Otherwise getTrack() timestamps the direct HTML-media read.
      const sampledAt = Number(track.sampledAt) > 0 ? Number(track.sampledAt) : Date.now();
      if (eventType === "seeked") lastSeekedAt = sampledAt;
      // A concrete video ID is required so the status and Listen to Song link
      // always refer to the same track. Wait for YouTube Music's player-bar link
      // rather than publishing a title with a missing or stale destination.
      if (!track.title || !track.artist || !track.videoId) {
        if (wasActive || pendingCandidate) {
          if (!noMetadataSince) noMetadataSince = sampledAt;
          if (sampledAt - noMetadataSince >= NO_METADATA_CLEAR_MS) sendClear(false);
        }
        return;
      }
      noMetadataSince = 0;

      const key = candidateKey(track);
      const metadataKey = [normalise(track.title), normalise(track.artist)].join("\u001f");
      const newCandidate = key !== lastPublishedTrackKey;

      if (newCandidate) {
        if (!pendingCandidate || pendingCandidate.key !== key) {
          pendingCandidate = { key, since: sampledAt, samples: 0 };
        }
        pendingCandidate.samples += 1;

        // Clear the previous activity as soon as a candidate transition is seen.
        // The new activity is sent once the active track identity stays stable.
        if (wasActive) sendClear(false);

        // Require a short stable metadata window, but do not require duration or
        // readyState: those can arrive after playback starts and used to suppress
        // every activity on player builds with a changed media wrapper.
        // The active player API ID is authoritative when available; otherwise
        // the player-bar/page URL fallback must remain stable for these samples.
        const stable = pendingCandidate.samples >= STABLE_SAMPLES_REQUIRED &&
          sampledAt - pendingCandidate.since >= STABLE_FOR_MS &&
          Boolean(track.title && track.videoId);
        if (!stable) return;

        lastPublishedTrackKey = key;
        lastPublishedMetadataKey = metadataKey;
        lastPublishedVideoId = track.videoId || "";
        pendingCandidate = null;
      } else {
        pendingCandidate = null;
        lastPublishedMetadataKey = metadataKey;
        lastPublishedVideoId = track.videoId || lastPublishedVideoId;
      }

      const fingerprint = [
        key,
        track.title,
        track.artist,
        track.album,
        track.art,
        track.playing ? "playing" : "paused",
        track.buffering ? "buffering" : "ready",
        Math.floor(track.position * 2),
        Math.floor(track.duration),
        Number(track.playbackRate || 1).toFixed(2),
        track.mediaState,
        track.videoIdSource
      ].join("\u001f");

      const shouldSend = force || fingerprint !== lastFingerprint || sampledAt - lastSentAt >= HEARTBEAT_MS;
      if (!shouldSend) return;

      lastFingerprint = fingerprint;
      lastSentAt = sampledAt;
      wasActive = true;
      sendBridgeMessage({
        type: "ytm-rpc-state",
        sampledAt,
        payload: {
          ...track,
          sampledAt,
          eventType,
          seekedAt: lastSeekedAt
      }
      });
    } catch (error) {
      console.debug("YTM Discord RPC sample failed", error);
    }
  }

  function attachMediaEvents() {
    const video = getVideo();
    if (!video || video === observedVideo) return;
    // A few SPA transitions replace the HTMLMediaElement. Do not let events from
    // a detached, previous-song element mutate the current player's state.
    if (detachObservedVideoListeners) detachObservedVideoListeners();
    observedVideo = video;
    mediaWaiting = false;

    const onMediaEvent = (event) => {
      if (event.type === "waiting" || event.type === "stalled") mediaWaiting = true;
      if (["canplay", "canplaythrough", "playing", "play"].includes(event.type)) mediaWaiting = false;
      if (event.type === "seeked") lastSeekedAt = Date.now();
      sampleAndSend(true, event.type);
    };

    const mediaEvents = [
      "loadedmetadata", "durationchange", "loadeddata", "canplay", "canplaythrough",
      "playing", "play", "pause", "waiting", "stalled", "seeking", "seeked",
      "ratechange", "ended", "emptied"
    ];
    mediaEvents.forEach((eventName) => video.addEventListener(eventName, onMediaEvent, { passive: true }));

    const onTimeUpdate = () => {
      // timeupdate follows the actual media clock; the polling heartbeat is only
      // a fallback for tab throttling and bridge liveness.
      sampleAndSend(false, "timeupdate");
    };
    video.addEventListener("timeupdate", onTimeUpdate, { passive: true });
    detachObservedVideoListeners = () => {
      mediaEvents.forEach((eventName) => video.removeEventListener(eventName, onMediaEvent));
      video.removeEventListener("timeupdate", onTimeUpdate);
    };
  }

  // Receive the small, same-tab snapshot from the MAIN-world player probe.
  // The message never leaves this extension until the ordinary local bridge send.
  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== location.origin) return;
    const message = event.data;
    if (message?.source !== "ytm-discord-rpc-main-world" || message.type !== "player-snapshot") return;
    const snapshot = message.payload;
    if (!snapshot || typeof snapshot !== "object") return;
    const videoId = String(snapshot.videoId || "");
    if (videoId && !/^[A-Za-z0-9_-]{6,20}$/.test(videoId)) return;
    const sampledAt = Number(snapshot.sampledAt || 0);
    if (!Number.isFinite(sampledAt) || sampledAt <= 0 || sampledAt > Date.now() + 5000) return;
    if (latestMainPlayerSnapshot && sampledAt < latestMainPlayerSnapshot.sampledAt) return;
    latestMainPlayerSnapshot = snapshot;
  }, false);

  // Poll for YouTube Music SPA track changes, but rely on native media events for
  // pause/resume/seek so those state changes are forwarded immediately.
  setInterval(() => {
    attachMediaEvents();
    sampleAndSend();
  }, POLL_MS);

  ["yt-navigate-finish", "yt-page-data-updated", "ytmusic-player-state-changed"].forEach((eventName) => {
    document.addEventListener(eventName, () => {
      setTimeout(() => { attachMediaEvents(); sampleAndSend(true, eventName); }, 100);
      setTimeout(() => sampleAndSend(true, eventName), 450);
      setTimeout(() => sampleAndSend(true, eventName), 1000);
    });
  });

  document.addEventListener("visibilitychange", () => sampleAndSend(true, "visibilitychange"));
  window.addEventListener("pagehide", () => sendClear(), { once: true });
  attachMediaEvents();
  sampleAndSend(true, "initial");
})();
