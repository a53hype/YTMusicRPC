// Run with: node tests/test_detection_runtime.js
// Exercises the MAIN-world probe -> isolated content script bridge for an
// already-playing song before HTML video metadata/duration is ready.
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const root = path.resolve(__dirname, "..");
const probeSource = fs.readFileSync(path.join(root, "extension", "player_probe.js"), "utf8");
const contentSource = fs.readFileSync(path.join(root, "extension", "content.js"), "utf8");

function runScenario(videoPresent, videoReady = false) {
  let now = 1_000_000;
  let probeTick;
  let contentTick;
  const sent = [];
  const listeners = [];
  const video = videoPresent ? {
    currentTime: 12.0,
    duration: 0,
    playbackRate: 1,
    readyState: videoReady ? 1 : 0,
    paused: false,
    ended: false,
    currentSrc: "blob:ytm",
    addEventListener() {},
    removeEventListener() {}
  } : null;
  const player = {
    getVideoData: () => ({ video_id: "abcdefghijk", title: "Already Playing Track", author: "Test Artist" }),
    getPlayerState: () => 1,
    getVideoUrl: () => "https://www.youtube.com/watch?v=abcdefghijk",
    getProgressState: () => ({ current: 31.5, duration: 200 }),
    getCurrentTime: () => 31.5,
    getDuration: () => 200
  };
  const document = {
    querySelector(selector) {
      if (selector === "#movie_player") return player;
      if (selector.includes("video") || selector === "video") return video;
      return null;
    },
    addEventListener() {}
  };
  const location = {
    hostname: "music.youtube.com",
    origin: "https://music.youtube.com",
    href: "https://music.youtube.com/"
  };
  const windowObject = {
    addEventListener(name, callback) {
      if (name === "message") listeners.push(callback);
    },
    setInterval(callback) {
      probeTick = callback;
      return 1;
    },
    postMessage(data) {
      for (const callback of listeners) {
        callback({ source: windowObject, origin: location.origin, data });
      }
    }
  };
  const clock = { now: () => now };
  const common = {
    window: windowObject,
    document,
    location,
    navigator: { mediaSession: { playbackState: "playing", metadata: null } },
    URL,
    Date: clock,
    Math,
    console
  };

  // The page-world probe is loaded before the isolated-world receiver.
  vm.runInNewContext(probeSource, { ...common });
  vm.runInNewContext(contentSource, {
    ...common,
    chrome: { runtime: { sendMessage: message => sent.push(message) } },
    setInterval(callback) {
      contentTick = callback;
      return 1;
    },
    setTimeout(callback) {
      callback();
      return 1;
    }
  });

  // The probe's first immediate message precedes the receiver. Later snapshots
  // arrive and are accepted, even though the media element is not ready yet.
  for (let index = 0; index < 5; index += 1) {
    now += 200;
    probeTick();
    now += 50; // Content script consumes the slightly older MAIN-world sample.
    contentTick();
  }

  const state = sent.find(message => message.type === "ytm-rpc-state");
  if (!state) throw new Error(`No state emitted; videoPresent=${videoPresent}`);
  if (state.payload.title !== "Already Playing Track" ||
      state.payload.artist !== "Test Artist" ||
      state.payload.videoId !== "abcdefghijk" ||
      state.payload.playing !== true ||
      state.payload.position !== 31.5) {
    throw new Error(`Wrong detected song: ${JSON.stringify(state.payload)}`);
  }
  if (state.sampledAt !== state.payload.sampledAt) {
    throw new Error(`Sample timestamp was not preserved: ${state.sampledAt} vs ${state.payload.sampledAt}`);
  }
  if (state.sampledAt === now) {
    throw new Error("Expected the original player sample timestamp, not a later content-script timestamp");
  }
}

runScenario(true);
runScenario(false);
runScenario(true, true); // The page API must win over a stale HTML video clock.
process.stdout.write("3/3 player-detection and media-clock runtime scenarios passed.\n");
