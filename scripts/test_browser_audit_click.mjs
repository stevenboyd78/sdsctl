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

class MovingTargetCdp {
  constructor() {
    this.geometryA = geometry({top: 100, bottom: 120});
    this.geometryB = geometry({top: 79.0625, bottom: 99.0625});
    this.initialMeasurements = 0;
    this.confirmedMeasurements = 0;
    this.armedMeasurements = 0;
    this.events = [];
  }

  result(value) {
    return {result: {value}};
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
      this.events.push({kind: "available"});
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
      const value = this.armedMeasurements++ === 0
        ? {...this.geometryB, stabilizedPointOwned: false}
        : {...this.geometryB, stabilizedPointOwned: true};
      this.events.push({kind: "post-arm-measurement", value});
      return this.result(value);
    }
    if (source.includes("pressedPointOwned")) {
      const value = {
        ...this.geometryB,
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
      const value = this.confirmedMeasurements++ < 2 ? this.geometryA : this.geometryB;
      this.events.push({kind: "hover-measurement", value});
      return this.result(value);
    }
    if (source.includes("const rect = element.getBoundingClientRect()")) {
      const measured = this.initialMeasurements++ === 0 ? this.geometryA : this.geometryB;
      const {hovered: _hovered, ...value} = measured;
      this.events.push({kind: "initial-measurement", value});
      return this.result(value);
    }
    assert.fail(`Unexpected Runtime.evaluate source: ${source}`);
  }
}

test("moving target is reacquired before exactly one trusted press and release", async () => {
  const cdp = new MovingTargetCdp();
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
    x: cdp.geometryB.x,
    y: cdp.geometryB.y,
  });
  assert.deepEqual({x: releases[0].x, y: releases[0].y}, {
    x: cdp.geometryB.x,
    y: cdp.geometryB.y,
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
