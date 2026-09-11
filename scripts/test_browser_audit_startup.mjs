import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import test from 'node:test';
import {captureChildOutput, waitForHttp} from './audit_web_dashboard_browser.mjs';

const url = 'http://127.0.0.1:12345/json/version';

test('readiness returns the exact successful response and keeps the request abort signal', async () => {
  const response = {ok: true};
  let requests = 0;
  const actual = await waitForHttp(url, 30000, null, {
    now: () => 0,
    fetcher: async (target, {signal}) => {
      requests++;
      assert.equal(target, url);
      assert.ok(signal instanceof AbortSignal);
      assert.equal(signal.aborted, false);
      return response;
    },
    sleep: async () => assert.fail('No delay after successful readiness'),
  });
  assert.equal(actual, response);
  assert.equal(requests, 1);
});

test('readiness failure reports request classes and timing without extending the deadline', async () => {
  let now = 0, requests = 0;
  await assert.rejects(waitForHttp(url, 300, {exitCode: null, signalCode: null}, {
    now: () => now,
    fetcher: async () => {
      requests++;
      now += 4;
      if (requests === 1) throw new TypeError('fetch failed', {cause: {code: 'ECONNREFUSED'}});
      if (requests === 2) return {ok: false, status: 503};
      throw new DOMException('fixture request expired', 'TimeoutError');
    },
    sleep: async (ms) => { assert.equal(ms, 100); now += ms; },
  }), error => {
    assert.match(error.message, /timed out waiting/);
    assert.deepEqual(JSON.parse(error.message.split('; readiness=')[1]), {
      elapsedMs: 312, timeoutMs: 300, attempts: 3,
      outcomes: {
        ECONNREFUSED: {count: 1, firstMs: 4, lastMs: 4},
        'HTTP 503': {count: 1, firstMs: 108, lastMs: 108},
        TimeoutError: {count: 1, firstMs: 212, lastMs: 212},
      }, exitCode: null, signalCode: null,
    });
    return true;
  });
  assert.equal(requests, 3);
});

test('repeated readiness errors retain first and last timing plus exact count', async () => {
  let now = 0;
  await assert.rejects(waitForHttp(url, 300, null, {
    now: () => now,
    fetcher: async () => { throw Object.assign(new Error('fixture'), {code: 'ECONNREFUSED'}); },
    sleep: async ms => { now += ms; },
  }), error => {
    const detail = JSON.parse(error.message.split('; readiness=')[1]);
    assert.equal(detail.attempts, 3);
    assert.deepEqual(detail.outcomes.ECONNREFUSED, {count: 3, firstMs: 0, lastMs: 200});
    return true;
  });
});

test('an exited child remains a failure without making an HTTP request', async () => {
  await assert.rejects(waitForHttp(url, 30000, {exitCode: 2, signalCode: null}, {
    now: () => 0,
    fetcher: async () => assert.fail('Child already exited'),
  }), error => {
    assert.match(error.message, /process exited before/);
    assert.equal(JSON.parse(error.message.split('; readiness=')[1]).exitCode, 2);
    assert.equal(JSON.parse(error.message.split('; readiness=')[1]).attempts, 0);
    return true;
  });
});

test('many distinct failures cannot make an unbounded readiness report', async () => {
  let now = 0, code = 0;
  await assert.rejects(waitForHttp(url, 2000, null, {
    now: () => now,
    fetcher: async () => { throw {code: String(code++).padEnd(500, 'x')}; },
    sleep: async ms => { now += ms; },
  }), error => {
    const detail = JSON.parse(error.message.split('; readiness=')[1]);
    assert.equal(detail.attempts, 20);
    assert.equal(Object.keys(detail.outcomes).length, 8);
    assert.ok(Object.keys(detail.outcomes).every(key => key.length === 80));
    return true;
  });
});

test('child output timestamps distinguish early listening from late readiness', () => {
  let now = 100;
  const child = Object.assign(new EventEmitter(), {
    stdout: new EventEmitter(), stderr: new EventEmitter(),
  });
  const output = captureChildOutput(child, () => now);
  child.emit('spawn');
  now = 150;
  child.stderr.emit('data', Buffer.from('DevTools listening\n'));
  now = 200;
  child.stdout.emit('data', 'fixture stdout\n');
  now = 250;
  child.emit('exit', null, 'SIGTERM');
  assert.equal(output(), '[+0ms] process spawned\n[+50ms] DevTools listening\n' +
    '[+100ms] fixture stdout\n[+150ms] process exit=null signal=SIGTERM\n');
});

test('large child chunks retain a bounded tail and spawn errors are captured', () => {
  const child = Object.assign(new EventEmitter(), {stderr: new EventEmitter()});
  const output = captureChildOutput(child, () => 0);
  child.stderr.emit('data', 'x'.repeat(20000) + 'last message\n');
  assert.equal(output().length, 16000);
  assert.ok(output().endsWith('last message\n'));
  child.emit('error', Object.assign(new Error('fixture spawn'), {code: 'ENOENT'}));
  assert.equal(output().length, 16000);
  assert.ok(output().endsWith('[+0ms] process error=ENOENT\n'));
});
