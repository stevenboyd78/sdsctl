"use strict";
const assert = require("node:assert/strict");
const vm = require("node:vm");
const source = require("node:fs").readFileSync(0, "utf8");
const scenario = process.argv[2];
class Node {
  constructor() { this.listeners = {}; this.disabled = false; this.textContent = ""; }
  addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
  emit(name) { for (const fn of this.listeners[name] ?? []) fn(); }
}
class Player extends Node {
  constructor() {
    super(); this.src = ""; this.paused = true; this.ended = false;
    this.error = null; this.currentTime = 0; this.plays = [];
  }
  getAttribute(name) { assert.equal(name, "src"); return this.src || null; }
  removeAttribute(name) { assert.equal(name, "src"); this.src = ""; }
  load() { this.paused = true; this.ended = false; this.error = null; this.currentTime = 0; }
  play() {
    this.paused = false; this.ended = false; this.emit("play");
    return new Promise((resolve, reject) => this.plays.push({resolve, reject}));
  }
  pause() { this.paused = true; this.emit("pause"); }
}
const player = new Player(), toggle = new Node(), stop = new Node(), status = new Node();
status.textContent = "No saved recording selected.";
const nodes = {"saved-recording-player":player, "saved-playback-toggle":toggle,
  "saved-playback-stop":stop, "saved-playback-status":status};
const page = new Node();
const context = {
  element(id) { assert.ok(id in nodes, id); return nodes[id]; },
  recordingName(id) { return id.split("/").at(-1); },
  recordingFileUrl(id) { return "/api/v1/recordings/file/" + id; },
  savedPlaybackGeneration: 0, authenticationRequired: false, displayOnly: false,
  window: page, mimicPageSuspended: false, nativeSessionTimer: null,
  mimicDisplay: {stop() {}}, currentDaemonHello: {},
  reconcileMimicDisplay() {}, clearHomeAssistantBridgeKey() {},
  clearHomeAssistantAdvancedSecrets() {}, stopEventStream() {},
  stopWaterfallStream() {}, stopAudioPlayback() {}, setScannerControls() {},
};
vm.createContext(context);
vm.runInContext(source, context);
const select = (name="first.wav") => context.playSavedRecording(name);
const playing = () => {
  assert.equal(player.paused, false);
  assert.equal(status.textContent, "Playing finalized recording.");
  assert.equal(toggle.textContent, "Pause saved recording");
  assert.equal(toggle.disabled, false); assert.equal(stop.disabled, false);
};
const paused = () => {
  assert.equal(player.paused, true);
  assert.equal(status.textContent, "Saved recording playback paused.");
  assert.equal(toggle.textContent, "Resume saved recording");
};
const stopped = () => {
  assert.equal(player.src, ""); assert.equal(player.paused, true);
  assert.equal(status.textContent, "Saved recording playback stopped.");
  assert.equal(toggle.disabled, true); assert.equal(stop.disabled, true);
};
async function run() {
  if (scenario === "initial") {
    assert.equal(toggle.disabled, true); assert.equal(stop.disabled, true);
    assert.equal(status.textContent, "No saved recording selected.");
    return;
  }
  if (["authentication", "display_only"].includes(scenario)) {
    context[scenario === "authentication" ? "authenticationRequired" : "displayOnly"] = true;
    select(); context.resumeSavedRecording();
    assert.equal(player.src, ""); assert.equal(player.plays.length, 0); return;
  }
  select(); playing(); player.currentTime = 12;
  switch (scenario) {
    case "pause_resume":
      toggle.emit("click"); paused(); assert.equal(player.currentTime, 12);
      toggle.emit("click"); playing(); assert.equal(player.currentTime, 12); break;
    case "native_pause":
      player.pause(); paused(); void player.play(); playing(); break;
    case "stop":
      stop.emit("click"); stopped(); assert.equal(player.currentTime, 0); break;
    case "ended":
      player.ended = true; player.paused = true; player.emit("pause"); player.emit("ended");
      assert.equal(status.textContent, "Saved recording playback finished.");
      assert.equal(toggle.textContent, "Replay saved recording");
      toggle.emit("click"); playing(); break;
    case "error":
      player.error = {code:3}; player.emit("error");
      assert.equal(status.textContent, "Saved recording playback failed.");
      assert.equal(toggle.disabled, true); assert.equal(stop.disabled, false);
      stop.emit("click"); stopped(); break;
    case "replacement_rejection":
      select("second.wav"); player.plays[0].reject(new Error("old source"));
      await Promise.resolve(); playing(); assert.ok(player.src.endsWith("second.wav")); break;
    case "pause_rejection":
      toggle.emit("click"); player.plays[0].reject(new Error("interrupted"));
      await Promise.resolve(); paused(); break;
    case "stop_rejection":
      stop.emit("click"); player.plays[0].reject(new Error("interrupted"));
      await Promise.resolve(); stopped(); break;
    case "play_failure":
      player.paused = true; player.plays[0].reject(new Error("private browser detail"));
      await Promise.resolve();
      assert.equal(status.textContent, "Saved recording playback failed.");
      assert.equal(toggle.textContent, "Resume saved recording"); break;
    case "stale_events":
      for (const name of ["pause", "ended", "error", "emptied"]) player.emit(name);
      playing(); stop.emit("click");
      for (const name of ["play", "pause", "ended", "error", "emptied"]) player.emit(name);
      stopped(); break;
    case "session_stop":
      context.authenticationRequired = true; context.stopNativeSessionActivity("Ended");
      player.plays[0].reject(new Error("late")); await Promise.resolve();
      stopped(); context.resumeSavedRecording(); assert.equal(player.plays.length, 1); break;
    case "pagehide":
      page.emit("pagehide"); player.plays[0].reject(new Error("late"));
      await Promise.resolve(); stopped(); assert.equal(context.mimicPageSuspended, true); break;
    default: throw new Error("Unknown scenario");
  }
}
run().then(() => console.log(scenario + " passed")).catch(error => { console.error(error); process.exitCode = 1; });
