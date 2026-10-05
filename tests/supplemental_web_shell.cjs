"use strict";
const assert = require("node:assert/strict");
const vm = require("node:vm");
const source = require("node:fs").readFileSync(0, "utf8");
const origin = process.argv[2];
for (const prefix of ["/", "/api/hassio_ingress/synthetic-token/"]) {
  for (const mode of [undefined, "cached", "demand", "invalid", ""]) {
    for (const missing of [null, "helper", "controller", "throws"]) {
      const calls = [], notices = [], controller = {stop() {}};
      const host = {prepend(value) { notices.push(value); }}, standard = {};
      const request = () => { throw new Error("Bootstrap must not fetch."); };
      const context = {
        window: {
          sdsctlSupplemental: missing === "helper" ? undefined : {},
          sdsctlMimic: missing === "controller" ? undefined : {create(options) {
            calls.push(options);
            if (missing === "throws") throw new Error("Synthetic initialization failure.");
            return controller;
          }},
        },
        document: {
          documentElement: {dataset: mode === undefined ? {} : {sdsctlSupplemental: mode}},
          createElement() { return {setAttribute(name, value) { this[name] = value; }}; },
        },
        element(id) { return id === "pane-scanner" ? host : standard; },
        webUrl(path) { return new URL(path, origin + prefix).href; },
        webRootUrl: new URL(origin + prefix),
        dashboardFetch: request, mimicDisplay: null,
      };
      vm.createContext(context);
      const boot = () => vm.runInContext(source + "\ninitializeMimicDisplay();", context);
      if (mode === undefined && missing === "throws") { assert.throws(boot); continue; }
      boot();
      if (mode !== undefined && (!["cached", "demand"].includes(mode) || missing !== null)) {
        assert.equal(context.mimicDisplay, null);
        assert.equal(calls.length, missing === "throws" && ["cached", "demand"].includes(mode) ? 1 : 0);
        assert.equal(notices.length, 1);
        assert.equal(notices[0].role, "alert");
        continue;
      }
      assert.equal(notices.length, 0);
      if (missing === "controller") { assert.equal(calls.length, 0); continue; }
      assert.equal(calls.length, 1);
      assert.equal(context.mimicDisplay, controller);
      const options = calls[0];
      assert.equal(options.request, request);
      assert.equal(options.host, host);
      assert.equal(options.standard, standard);
      assert.equal(options.url, origin + prefix + "api/v1/display-frame");
      if (mode === undefined) {
        assert.equal("supplementalRoot" in options, false);
        assert.equal("supplementalDemand" in options, false);
      } else {
        assert.equal(options.supplementalRoot, origin + prefix);
        assert.equal(options.supplementalDemand, mode === "demand");
      }
    }
  }
}
console.log("Actual dashboard bootstrap selection and failure isolation passed.");
