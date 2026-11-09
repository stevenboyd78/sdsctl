import assert from "node:assert/strict";
import test from "node:test";
import {clickElement} from "./audit_web_dashboard_browser.mjs";

function geometry({top, bottom}) {
  const left = 10;
  const right = 30;
  return {
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
  constructor({armed, confirmed, initial, pressGeometry}) {
    this.armedScript = armed;
    this.confirmedScript = confirmed;
    this.initialScript = initial;
    this.pressGeometry = pressGeometry;
    this.initialMeasurements = 0;
    this.confirmedMeasurements = 0;
    this.armedMeasurements = 0;
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

  async send(method, parameters) {
    if (method === "Input.dispatchMouseEvent") {
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
        preventScroll: source.includes("element.focus({preventScroll: true})"),
      });
      return this.result(true);
    }
    if (source.includes("const record = {clicked: null")) {
      this.events.push({kind: "witness-armed"});
      return this.result(true);
    }
    if (source.includes("trusted-click-witness cleanup before geometry reacquisition")) {
      this.events.push({kind: "witness-disarmed"});
      return this.result(true);
    }
    if (source.includes("stabilizedPointOwned")) {
      const value = this.take(
        this.armedScript,
        "armedMeasurements",
        "post-arm",
      );
      this.events.push({kind: "post-arm-measurement", value});
      return this.result(value);
    }
    if (source.includes("pressedPointOwned")) {
      const value = {
        ...this.pressGeometry,
        active: true,
        centerOwned: true,
        focused: true,
        pressedPointOwned: true,
      };
      this.events.push({kind: "press-receipt", value});
      return this.result(value);
    }
    if (source.includes("return record?.token ===")) {
      this.events.push({kind: "witness-read"});
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

test("target focus is settled without scrolling before initial geometry", async () => {
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
    preventScroll: true,
  });
  assert.ok(cdp.events.indexOf(preparation) < cdp.events.indexOf(frame));
  assert.ok(cdp.events.indexOf(frame) < cdp.events.indexOf(initial));
  assert.ok(cdp.events.indexOf(initial) < cdp.events.indexOf(press));
  assert.equal(cdp.events.filter(event => event.type === "mousePressed").length, 1);
  assert.equal(cdp.events.filter(event => event.type === "mouseReleased").length, 1);
  assert.equal(cdp.events.filter(event => event.kind === "witness-read").length, 1);
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
  assert.equal(witnessDisarms.length, 1);
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
  assert.equal(witnessDisarms.length, 1);
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
  const witnessRead = cdp.events.findIndex(event => event.kind === "witness-read");
  assert.ok(firstPostArm < disarm);
  assert.ok(disarm < secondArm);
  assert.ok(secondArm < secondPostArm);
  assert.ok(secondPostArm < press);
  assert.ok(press < release);
  assert.ok(release < witnessRead);
});

test("persistent oscillation exhausts the bounded budget without a press", async () => {
  const cdp = oscillatingTarget({finalStable: false});

  await assert.rejects(
    clickElement(cdp, "#fixture-target"),
    /after 3 settled attempts with 3 pointer moves each and post-arm checks/,
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
