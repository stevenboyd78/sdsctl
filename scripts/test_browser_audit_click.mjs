import assert from "node:assert/strict";
import test from "node:test";
import {clickElement} from "./audit_web_dashboard_browser.mjs";

function geometry({top, bottom}) {
  const left = 10;
  const right = 30;
  return {
    focused: true,
    hit: {classes: [], id: "fixture-target", tag: "button"},
    hovered: true,
    owned: true,
    rect: {bottom, left, right, top},
    x: (left + right) / 2,
    y: (top + bottom) / 2,
  };
}

const GEOMETRY_A = geometry({top: 100, bottom: 120});
const GEOMETRY_B = geometry({top: 79.0625, bottom: 99.0625});

class ScriptedTargetCdp {
  constructor({
    armed,
    confirmed,
    dispatch = null,
    failures = {},
    initial,
    pressGeometry,
    releaseClicks = null,
  }) {
    this.armedScript = armed;
    this.confirmedScript = confirmed;
    this.dispatchScript = dispatch;
    this.initialScript = initial;
    this.pressGeometryScript = Array.isArray(pressGeometry)
      ? pressGeometry
      : [pressGeometry];
    this.releaseClicks = releaseClicks;
    this.initialMeasurements = 0;
    this.confirmedMeasurements = 0;
    this.armedMeasurements = 0;
    this.dispatchMeasurements = 0;
    this.pressMeasurements = 0;
    this.releaseMeasurements = 0;
    this.failures = {...failures};
    this.lastArmedValue = null;
    this.clickRecorded = null;
    this.witnessActive = false;
    this.events = [];
  }

  result(value) {
    return {result: {value}};
  }

  take(script, counter, kind) {
    const index = this[counter]++;
    assert.ok(index < script.length, `Unexpected ${kind} measurement ${index + 1}`);
    return script[index];
  }

  maybeReject(kind) {
    const remaining = this.failures[kind] ?? 0;
    if (remaining <= 0) return;
    this.failures[kind] = remaining - 1;
    this.events.push({kind: `${kind}-rejected`});
    throw new Error(`injected ${kind} failure`);
  }

  async send(method, parameters) {
    if (method === "Input.dispatchMouseEvent") {
      if (parameters.type === "mouseReleased" && this.witnessActive) {
        const index = this.releaseMeasurements++;
        this.clickRecorded = this.releaseClicks === null
          ? {isTrusted: true, owned: true, target: {id: "fixture-target"}}
          : this.releaseClicks[index];
      }
      this.events.push({method, ...parameters});
      return {};
    }
    assert.equal(method, "Runtime.evaluate");
    const source = parameters.expression;
    if (source.includes("new Promise((resolve)")) {
      this.events.push({kind: "frame"});
      return this.result(true);
    }
    if (source.includes("element.scrollIntoView")) {
      this.events.push({
        focusBeforeMeasurement: source.indexOf("element.focus") >
          source.indexOf("element.scrollIntoView"),
        focusVerified: source.includes("document.activeElement === element"),
        kind: "target-prepared",
        nativeFocusScroll: source.includes("element.focus({preventScroll: false})"),
      });
      return this.result(true);
    }
    if (source.includes("listenerTarget: document")) {
      this.events.push({kind: "witness-armed"});
      this.clickRecorded = null;
      this.witnessActive = true;
      return this.result(true);
    }
    if (source.includes("trusted-click-witness cleanup")) {
      this.events.push({kind: "witness-disarmed"});
      const matched = this.witnessActive;
      const clicked = matched ? this.clickRecorded : null;
      this.witnessActive = false;
      return this.result({clicked, matched});
    }
    if (source.includes("dispatchPointOwned")) {
      const index = this.dispatchMeasurements++;
      const scripted = this.dispatchScript === null
        ? {
          ...this.lastArmedValue,
          dispatchPointOwned: true,
        }
        : this.dispatchScript[index];
      assert.notEqual(scripted, undefined, `Unexpected final-dispatch measurement ${index + 1}`);
      const value = {
        focused: true,
        witnessCurrent: true,
        ...scripted,
      };
      this.events.push({kind: "final-dispatch-measurement", value});
      return this.result(value);
    }
    if (source.includes("stabilizedPointOwned")) {
      this.maybeReject("post-arm");
      const scripted = this.take(
        this.armedScript,
        "armedMeasurements",
        "post-arm",
      );
      const value = {
        focused: true,
        witnessCurrent: true,
        ...scripted,
      };
      this.lastArmedValue = value;
      this.events.push({kind: "post-arm-measurement", value});
      return this.result(value);
    }
    if (source.includes("pressedPointOwned")) {
      this.maybeReject("press-receipt");
      const scripted = this.take(
        this.pressGeometryScript,
        "pressMeasurements",
        "press receipt",
      );
      const value = {
        active: true,
        centerOwned: true,
        focused: true,
        pressedPointOwned: true,
        ...scripted,
      };
      this.events.push({kind: "press-receipt", value});
      return this.result(value);
    }
    if (source.includes("return record?.token ===")) {
      this.events.push({kind: "witness-read"});
      this.witnessActive = false;
      return this.result({isTrusted: true, owned: true, target: {id: "fixture-target"}});
    }
    if (source.includes('hovered: element.matches(":hover")')) {
      const value = this.take(
        this.confirmedScript,
        "confirmedMeasurements",
        "hover",
      );
      this.events.push({kind: "hover-measurement", value});
      return this.result(value);
    }
    if (source.includes("const rect = element.getBoundingClientRect()")) {
      const measured = this.take(
        this.initialScript,
        "initialMeasurements",
        "initial",
      );
      const {hovered: _hovered, ...value} = measured;
      this.events.push({kind: "initial-measurement", value});
      return this.result(value);
    }
    assert.fail(`Unexpected Runtime.evaluate source: ${source}`);
  }
}

test("target native focus scroll is settled before initial geometry", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [{...GEOMETRY_A, stabilizedPointOwned: true}],
    confirmed: [GEOMETRY_A, GEOMETRY_A],
    initial: [GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });
  await clickElement(cdp, "#fixture-target");

  const preparation = cdp.events.find(event => event.kind === "target-prepared");
  const frame = cdp.events.find(event => event.kind === "frame");
  const initial = cdp.events.find(event => event.kind === "initial-measurement");
  const press = cdp.events.find(event => event.type === "mousePressed");

  assert.deepEqual(preparation, {
    focusBeforeMeasurement: true,
    focusVerified: true,
    kind: "target-prepared",
    nativeFocusScroll: true,
  });
  assert.ok(cdp.events.indexOf(preparation) < cdp.events.indexOf(frame));
  assert.ok(cdp.events.indexOf(frame) < cdp.events.indexOf(initial));
  assert.ok(cdp.events.indexOf(initial) < cdp.events.indexOf(press));
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 1);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 1);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 1);
  assert.equal(cdp.witnessActive, false);
});

test("moving target is reacquired before exactly one trusted press and release", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_B, stabilizedPointOwned: false},
      {...GEOMETRY_B, stabilizedPointOwned: true},
    ],
    confirmed: [GEOMETRY_A, GEOMETRY_A, GEOMETRY_B, GEOMETRY_B],
    initial: [GEOMETRY_A, GEOMETRY_B],
    pressGeometry: GEOMETRY_B,
  });
  await clickElement(cdp, "#fixture-target");

  const presses = cdp.events.filter(event => event.type === "mousePressed");
  const releases = cdp.events.filter(event => event.type === "mouseReleased");
  const witnessArms = cdp.events.filter(event => event.kind === "witness-armed");
  const witnessDisarms = cdp.events.filter(event => event.kind === "witness-disarmed");
  const postArmChecks = cdp.events.filter(event => event.kind === "post-arm-measurement");

  assert.equal(cdp.initialMeasurements, 2);
  assert.equal(cdp.confirmedMeasurements, 4);
  assert.equal(witnessArms.length, 2);
  assert.equal(witnessDisarms.length, 2);
  assert.equal(postArmChecks.length, 2);
  assert.equal(presses.length, 1);
  assert.equal(releases.length, 1);
  assert.deepEqual({x: presses[0].x, y: presses[0].y}, {
    x: GEOMETRY_B.x,
    y: GEOMETRY_B.y,
  });
  assert.deepEqual({x: releases[0].x, y: releases[0].y}, {
    x: GEOMETRY_B.x,
    y: GEOMETRY_B.y,
  });

  const firstPostArm = cdp.events.indexOf(postArmChecks[0]);
  const disarm = cdp.events.indexOf(witnessDisarms[0]);
  const secondArm = cdp.events.indexOf(witnessArms[1]);
  const secondPostArm = cdp.events.indexOf(postArmChecks[1]);
  const press = cdp.events.indexOf(presses[0]);
  assert.ok(firstPostArm < disarm);
  assert.ok(disarm < secondArm);
  assert.ok(secondArm < secondPostArm);
  assert.ok(secondPostArm < press);
});

function oscillatingTarget({finalStable}) {
  return new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, stabilizedPointOwned: false},
      {
        ...(finalStable ? GEOMETRY_A : GEOMETRY_B),
        stabilizedPointOwned: finalStable,
      },
    ],
    confirmed: [
      GEOMETRY_A,
      {...GEOMETRY_B, hovered: false},
      GEOMETRY_B,
      GEOMETRY_B,
      GEOMETRY_B,
      GEOMETRY_A,
      GEOMETRY_A,
    ],
    initial: [GEOMETRY_A, GEOMETRY_B, GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });
}

function pointerAwayEvents(cdp) {
  return cdp.events.filter(
    event => event.type === "mouseMoved" && event.x === 0 && event.y === 0,
  );
}

test("oscillating target gets one final bounded reacquisition", async () => {
  const cdp = oscillatingTarget({finalStable: true});
  await clickElement(cdp, "#fixture-target");

  const presses = cdp.events.filter(event => event.type === "mousePressed");
  const releases = cdp.events.filter(event => event.type === "mouseReleased");
  const witnessArms = cdp.events.filter(event => event.kind === "witness-armed");
  const witnessDisarms = cdp.events.filter(event => event.kind === "witness-disarmed");
  const postArmChecks = cdp.events.filter(event => event.kind === "post-arm-measurement");

  assert.equal(cdp.initialMeasurements, 3);
  assert.equal(cdp.confirmedMeasurements, 7);
  assert.equal(witnessArms.length, 2);
  assert.equal(witnessDisarms.length, 2);
  assert.equal(postArmChecks.length, 2);
  assert.equal(pointerAwayEvents(cdp).length, 2);
  assert.equal(presses.length, 1);
  assert.equal(releases.length, 1);
  assert.deepEqual({x: presses[0].x, y: presses[0].y}, {
    x: GEOMETRY_A.x,
    y: GEOMETRY_A.y,
  });
  assert.deepEqual({x: releases[0].x, y: releases[0].y}, {
    x: GEOMETRY_A.x,
    y: GEOMETRY_A.y,
  });

  const firstPostArm = cdp.events.indexOf(postArmChecks[0]);
  const disarm = cdp.events.indexOf(witnessDisarms[0]);
  const secondArm = cdp.events.indexOf(witnessArms[1]);
  const secondPostArm = cdp.events.indexOf(postArmChecks[1]);
  const press = cdp.events.indexOf(presses[0]);
  const release = cdp.events.indexOf(releases[0]);
  const finalCleanup = cdp.events.indexOf(witnessDisarms[1]);
  assert.ok(firstPostArm < disarm);
  assert.ok(disarm < secondArm);
  assert.ok(secondArm < secondPostArm);
  assert.ok(secondPostArm < press);
  assert.ok(press < release);
  assert.ok(release < finalCleanup);
  assert.equal(cdp.witnessActive, false);
});

test("persistent oscillation exhausts the bounded budget without a press", async () => {
  const cdp = oscillatingTarget({finalStable: false});

  await assert.rejects(
    clickElement(cdp, "#fixture-target"),
    /after 3 settled attempts with 3 acquisition moves each plus final alignment and post-arm\/final-dispatch checks/,
  );

  assert.equal(cdp.initialMeasurements, 3);
  assert.equal(cdp.confirmedMeasurements, 7);
  assert.equal(cdp.armedMeasurements, 2);
  assert.equal(pointerAwayEvents(cdp).length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 2);
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 0);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 0);
  assert.equal(cdp.events.filter(event => event.kind === "witness-read").length, 0);
});

test("post-arm focus loss is reacquired before physical dispatch", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, focused: false, stabilizedPointOwned: true},
      {...GEOMETRY_A, stabilizedPointOwned: true},
    ],
    confirmed: [GEOMETRY_A, GEOMETRY_A, GEOMETRY_A, GEOMETRY_A],
    initial: [GEOMETRY_A, GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });

  await clickElement(cdp, "#fixture-target");

  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "final-dispatch-measurement").length, 1);
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 1);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 1);
});

test("post-arm witness identity loss is reacquired before physical dispatch", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, stabilizedPointOwned: true, witnessCurrent: false},
      {...GEOMETRY_A, stabilizedPointOwned: true},
    ],
    confirmed: [GEOMETRY_A, GEOMETRY_A, GEOMETRY_A, GEOMETRY_A],
    initial: [GEOMETRY_A, GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });

  await clickElement(cdp, "#fixture-target");

  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "final-dispatch-measurement").length, 1);
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 1);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 1);
  assert.equal(cdp.witnessActive, false);
});

test("post-arm protocol rejection disarms with zero physical dispatch", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [{...GEOMETRY_A, stabilizedPointOwned: true}],
    confirmed: [GEOMETRY_A, GEOMETRY_A],
    failures: {"post-arm": 1},
    initial: [GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });

  await assert.rejects(
    clickElement(cdp, "#fixture-target"),
    /injected post-arm failure/,
  );

  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 1);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 1);
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 0);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 0);
  assert.equal(cdp.witnessActive, false);
});

test("final pre-dispatch shift is reacquired before one trusted click", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, stabilizedPointOwned: true},
      {...GEOMETRY_B, stabilizedPointOwned: true},
    ],
    confirmed: [GEOMETRY_A, GEOMETRY_A, GEOMETRY_B, GEOMETRY_B],
    dispatch: [
      {...GEOMETRY_B, dispatchPointOwned: false},
      {...GEOMETRY_B, dispatchPointOwned: true},
    ],
    initial: [GEOMETRY_A, GEOMETRY_B],
    pressGeometry: GEOMETRY_B,
  });

  await clickElement(cdp, "#fixture-target");

  const finalChecks = cdp.events.filter(
    event => event.kind === "final-dispatch-measurement",
  );
  const postArmChecks = cdp.events.filter(event => event.kind === "post-arm-measurement");
  const witnessArms = cdp.events.filter(event => event.kind === "witness-armed");
  const witnessDisarms = cdp.events.filter(event => event.kind === "witness-disarmed");
  const presses = cdp.events.filter(event => event.type === "mousePressed");
  const releases = cdp.events.filter(event => event.type === "mouseReleased");

  assert.equal(finalChecks.length, 2);
  assert.equal(witnessArms.length, 2);
  assert.equal(witnessDisarms.length, 2);
  assert.equal(presses.length, 1);
  assert.equal(releases.length, 1);
  assert.deepEqual({x: presses[0].x, y: presses[0].y}, {
    x: GEOMETRY_B.x,
    y: GEOMETRY_B.y,
  });

  const firstPostArm = cdp.events.indexOf(postArmChecks[0]);
  const firstFinalMove = cdp.events.findIndex(
    (event, index) => index > firstPostArm && event.type === "mouseMoved" &&
      event.x === GEOMETRY_A.x && event.y === GEOMETRY_A.y,
  );
  const firstFinalCheck = cdp.events.indexOf(finalChecks[0]);
  const disarm = cdp.events.indexOf(witnessDisarms[0]);
  const secondArm = cdp.events.indexOf(witnessArms[1]);
  const secondPostArm = cdp.events.indexOf(postArmChecks[1]);
  const secondFinalCheck = cdp.events.indexOf(finalChecks[1]);
  const press = cdp.events.indexOf(presses[0]);
  assert.ok(firstPostArm < firstFinalMove);
  assert.ok(firstFinalMove < firstFinalCheck);
  assert.ok(firstFinalCheck < disarm);
  assert.ok(disarm < secondArm);
  assert.ok(secondArm < secondPostArm);
  assert.ok(secondPostArm < secondFinalCheck);
  assert.ok(secondFinalCheck < press);
});

test("final-dispatch witness identity loss is reacquired before one trusted click", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, stabilizedPointOwned: true},
      {...GEOMETRY_A, stabilizedPointOwned: true},
    ],
    confirmed: [GEOMETRY_A, GEOMETRY_A, GEOMETRY_A, GEOMETRY_A],
    dispatch: [
      {...GEOMETRY_A, dispatchPointOwned: true, witnessCurrent: false},
      {...GEOMETRY_A, dispatchPointOwned: true},
    ],
    initial: [GEOMETRY_A, GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });

  await clickElement(cdp, "#fixture-target");

  assert.equal(cdp.events.filter(event => event.kind === "final-dispatch-measurement").length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 2);
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 1);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 1);
  assert.equal(cdp.witnessActive, false);
});

test("persistent final-dispatch witness identity loss exhausts without a press", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, stabilizedPointOwned: true},
      {...GEOMETRY_A, stabilizedPointOwned: true},
      {...GEOMETRY_A, stabilizedPointOwned: true},
    ],
    confirmed: [
      GEOMETRY_A,
      GEOMETRY_A,
      GEOMETRY_A,
      GEOMETRY_A,
      GEOMETRY_A,
      GEOMETRY_A,
    ],
    dispatch: [
      {...GEOMETRY_A, dispatchPointOwned: true, witnessCurrent: false},
      {...GEOMETRY_A, dispatchPointOwned: true, witnessCurrent: false},
      {...GEOMETRY_A, dispatchPointOwned: true, witnessCurrent: false},
    ],
    initial: [GEOMETRY_A, GEOMETRY_A, GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });

  await assert.rejects(
    clickElement(cdp, "#fixture-target"),
    /center remained unstable before trusted click dispatch/,
  );

  assert.equal(cdp.events.filter(event => event.kind === "final-dispatch-measurement").length, 3);
  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 3);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 3);
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 0);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 0);
  assert.equal(cdp.witnessActive, false);
});

test("post-press protocol rejection still releases and cleans the witness", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [{...GEOMETRY_A, stabilizedPointOwned: true}],
    confirmed: [GEOMETRY_A, GEOMETRY_A],
    failures: {"press-receipt": 1},
    initial: [GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });

  await assert.rejects(
    clickElement(cdp, "#fixture-target"),
    /injected press-receipt failure/,
  );

  const presses = cdp.events.filter(event => event.type === "mousePressed");
  const releases = cdp.events.filter(event => event.type === "mouseReleased");
  const cleanup = cdp.events.filter(event => event.kind === "witness-disarmed");
  assert.equal(presses.length, 1);
  assert.equal(releases.length, 1);
  assert.deepEqual({x: releases[0].x, y: releases[0].y}, {
    x: -1,
    y: -1,
  });
  assert.equal(releases[0].button, "none");
  assert.equal(releases[0].clickCount, 0);
  assert.equal(cleanup.length, 1);
  assert.ok(cdp.events.indexOf(presses[0]) < cdp.events.indexOf(releases[0]));
  assert.ok(cdp.events.indexOf(releases[0]) < cdp.events.indexOf(cleanup[0]));
  assert.equal(cdp.witnessActive, false);
});

test("post-press movement retries once after a document witness proves no click", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, stabilizedPointOwned: true},
      {...GEOMETRY_B, stabilizedPointOwned: true},
    ],
    confirmed: [GEOMETRY_A, GEOMETRY_A, GEOMETRY_B, GEOMETRY_B],
    initial: [GEOMETRY_A, GEOMETRY_B],
    pressGeometry: [
      {
        ...GEOMETRY_B,
        active: false,
        focused: false,
        pressedPointOwned: false,
      },
      GEOMETRY_B,
    ],
    releaseClicks: [
      null,
      {isTrusted: true, owned: true, target: {id: "fixture-target"}},
    ],
  });

  await clickElement(cdp, "#fixture-target");

  const presses = cdp.events.filter(event => event.type === "mousePressed");
  const releases = cdp.events.filter(event => event.type === "mouseReleased");
  assert.equal(presses.length, 2);
  assert.equal(releases.length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 2);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 2);
  assert.equal(pointerAwayEvents(cdp).length, 1);
  assert.deepEqual({x: releases[0].x, y: releases[0].y}, {x: -1, y: -1});
  assert.equal(releases[0].button, "none");
  assert.equal(releases[0].clickCount, 0);
  assert.deepEqual({x: presses[1].x, y: presses[1].y}, {
    x: GEOMETRY_B.x,
    y: GEOMETRY_B.y,
  });
  assert.deepEqual({x: releases[1].x, y: releases[1].y}, {
    x: GEOMETRY_B.x,
    y: GEOMETRY_B.y,
  });
  assert.equal(releases[1].button, "left");
  assert.equal(releases[1].clickCount, 1);
  assert.equal(cdp.witnessActive, false);
});

test("post-press movement never retries after any click was witnessed", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [{...GEOMETRY_A, stabilizedPointOwned: true}],
    confirmed: [GEOMETRY_A, GEOMETRY_A],
    initial: [GEOMETRY_A],
    pressGeometry: [{
      ...GEOMETRY_B,
      active: false,
      focused: false,
      pressedPointOwned: false,
    }],
    releaseClicks: [
      {isTrusted: true, owned: false, target: {id: "other-control"}},
    ],
  });

  await assert.rejects(
    clickElement(cdp, "#fixture-target"),
    /did not complete one owned trusted click/,
  );

  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 1);
  const releases = cdp.events.filter(event => event.type === "mouseReleased");
  assert.equal(releases.length, 1);
  assert.deepEqual({x: releases[0].x, y: releases[0].y}, {x: -1, y: -1});
  assert.equal(releases[0].button, "none");
  assert.equal(releases[0].clickCount, 0);
  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 1);
  assert.equal(pointerAwayEvents(cdp).length, 0);
  assert.equal(cdp.witnessActive, false);
});

test("persistent post-press movement exhausts one safe retry", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, stabilizedPointOwned: true},
      {...GEOMETRY_B, stabilizedPointOwned: true},
    ],
    confirmed: [GEOMETRY_A, GEOMETRY_A, GEOMETRY_B, GEOMETRY_B],
    initial: [GEOMETRY_A, GEOMETRY_B],
    pressGeometry: [
      {
        ...GEOMETRY_B,
        active: false,
        focused: false,
        pressedPointOwned: false,
      },
      {
        ...GEOMETRY_A,
        active: false,
        focused: false,
        pressedPointOwned: false,
      },
    ],
    releaseClicks: [null, null],
  });

  await assert.rejects(
    clickElement(cdp, "#fixture-target"),
    /"physicalAttempt":2/,
  );

  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 2);
  const releases = cdp.events.filter(event => event.type === "mouseReleased");
  assert.equal(releases.length, 2);
  assert.deepEqual(
    releases.map(({x, y}) => ({x, y})),
    [{x: -1, y: -1}, {x: -1, y: -1}],
  );
  assert.deepEqual(
    releases.map(({button, clickCount}) => ({button, clickCount})),
    [{button: "none", clickCount: 0}, {button: "none", clickCount: 0}],
  );
  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 2);
  assert.equal(pointerAwayEvents(cdp).length, 1);
  assert.equal(cdp.witnessActive, false);
});

test("persistent final pre-dispatch movement exhausts without a press", async () => {
  const cdp = new ScriptedTargetCdp({
    armed: [
      {...GEOMETRY_A, stabilizedPointOwned: true},
      {...GEOMETRY_B, stabilizedPointOwned: true},
      {...GEOMETRY_A, stabilizedPointOwned: true},
    ],
    confirmed: [
      GEOMETRY_A,
      GEOMETRY_A,
      GEOMETRY_B,
      GEOMETRY_B,
      GEOMETRY_A,
      GEOMETRY_A,
    ],
    dispatch: [
      {...GEOMETRY_B, dispatchPointOwned: false},
      {...GEOMETRY_A, dispatchPointOwned: false},
      {...GEOMETRY_B, dispatchPointOwned: false},
    ],
    initial: [GEOMETRY_A, GEOMETRY_B, GEOMETRY_A],
    pressGeometry: GEOMETRY_A,
  });

  await assert.rejects(
    clickElement(cdp, "#fixture-target"),
    /after 3 settled attempts with 3 acquisition moves each plus final alignment and post-arm\/final-dispatch checks/,
  );

  assert.equal(cdp.dispatchMeasurements, 3);
  assert.equal(cdp.events.filter(event => event.kind === "witness-armed").length, 3);
  assert.equal(cdp.events.filter(event => event.kind === "witness-disarmed").length, 3);
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 0);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 0);
  assert.equal(cdp.events.filter(event => event.kind === "witness-read").length, 0);
});
