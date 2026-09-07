import assert from "node:assert/strict";
import test from "node:test";
import {connectBrowserSetupPage} from "../../src/sds200/browser_assets/browser_device_setup.mjs";

function fixture(response = {mode: "ready"}) {
  let listener;
  const f = {calls: [], form: {addEventListener: (name, fn) => {
    assert.equal(name, "submit"); listener = fn;
  }}, confirm: {checked: true}, button: {disabled: false}, notice: {textContent: ""}};
  const elements = {"setup-form": f.form, confirm: f.confirm, initialize: f.button, notice: f.notice};
  f.document = {getElementById: name => elements[name]};
  f.runtime = {getURL: page => `chrome-extension://${"a".repeat(32)}/${page}`,
    sendMessage: async message => { f.calls.push(message);
      if (response instanceof Error) throw response;
      return response;
    }};
  f.window = {location: {href: f.runtime.getURL("setup.html")}};
  f.window.top = f.window;
  f.submit = async (trusted = true) => {
    let prevented = false;
    listener({isTrusted: trusted, preventDefault: () => { prevented = true; }});
    await new Promise(resolve => setImmediate(resolve));
    assert(prevented);
  };
  return f;
}

test("setup needs a real confirmed submit, sends no secrets, and only submits once", async () => {
  const f = fixture(); connectBrowserSetupPage(f);
  await f.submit(false); assert.deepEqual(f.calls, []);
  f.confirm.checked = false; await f.submit(); assert.deepEqual(f.calls, []);
  f.confirm.checked = true; await f.submit(); await f.submit();
  assert.deepEqual(f.calls, [{action: "initialize"}]);
  assert(f.button.disabled);
  assert.match(f.notice.textContent, /No login was attempted/);
});

for (const [response, text] of [[{mode: "paused"}, /remains paused/],
  [{mode: "setup_refused"}, /Nothing was reset/], [{mode: "setup_error"}, /could not be confirmed/],
  [null, /could not be confirmed/], [{mode: "private-secret"}, /could not be confirmed/],
  [new Error("private-secret"), /acknowledgement was lost/]]) {
  test(`setup renders only fixed, redacted results: ${text}`, async () => {
    const f = fixture(response); connectBrowserSetupPage(f); await f.submit();
    assert.match(f.notice.textContent, text);
    assert(!f.notice.textContent.includes("private-secret"));
    assert(f.button.disabled); await f.submit(); assert.equal(f.calls.length, 1);
  });
}

test("setup refuses embedded or noncanonical pages", () => {
  for (const change of [f => { f.window.top = {}; },
    f => { f.window.location.href += "?extra"; },
    f => { f.window.location.href = "https://example.invalid/"; }]) {
    const f = fixture(); change(f); assert.throws(() => connectBrowserSetupPage(f));
    assert.deepEqual(f.calls, []);
  }
});
