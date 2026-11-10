import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  captureChildOutput,
  parseArguments,
  serializeFindingsReport,
  stopChild,
  waitForHttp,
  writeFindingsReport,
} from './audit_web_dashboard_browser.mjs';

const url = 'http://127.0.0.1:12345/json/version';

test('Chrome startup and later browser operations have independent deadlines', () => {
  const defaults = parseArguments(['--timeout-ms', '30000']);
  assert.equal(defaults.timeoutMs, 30000);
  assert.equal(defaults.startupTimeoutMs, 90000);

  const explicit = parseArguments([
    '--startup-timeout-ms', '75000',
    '--timeout-ms', '25000',
  ]);
  assert.equal(explicit.startupTimeoutMs, 75000);
  assert.equal(explicit.timeoutMs, 25000);
});

test('complete findings JSON is deterministic and retains every failure', () => {
  const result = {
    caseCount: 2,
    failures: [
      {context: 'system/800x480@1/scanner', message: 'first fixture'},
      {context: 'lcars/390x844@2/recordings', message: 'second fixture'},
    ],
    hoverContrastCases: 2,
    ingressDiagnosticsCases: 3,
    ingressHomeAssistantCases: 3,
    lcarsNavigationTransitionCases: 3,
    panoramaBoundaryCases: 30,
    phoneUnknownHoldCases: 28,
    systemPaletteCases: 210,
  };
  const serialized = serializeFindingsReport(result);
  assert.ok(serialized.endsWith('\n'));
  const report = JSON.parse(serialized);
  assert.equal(report.schemaVersion, 1);
  assert.equal(report.audit, 'web-dashboard-browser');
  assert.deepEqual(report.result, result);
  assert.equal(report.result.failures.length, 2);
  assert.equal(report.matrix.themes.length, 28);
  assert.equal(report.matrix.viewports.length, 5);
  assert.equal(report.matrix.panes.length, 6);
  assert.equal(serializeFindingsReport(result), serialized);
});

test('complete findings JSON writes every failure into a nested directory', async () => {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), 'sdsctl-findings-test-'));
  const outputPath = path.join(temporaryRoot, 'nested', 'browser-findings.json');
  const result = {
    caseCount: 1,
    failures: Array.from({length: 205}, (_, index) => ({
      context: `fixture/${index}`,
      message: `finding ${index}`,
    })),
  };
  try {
    await writeFindingsReport(outputPath, result);
    const report = JSON.parse(await readFile(outputPath, 'utf8'));
    assert.deepEqual(report.result, result);
    assert.equal(report.result.failures.length, 205);
    assert.equal(report.result.failures.at(-1).message, 'finding 204');
  } finally {
    await rm(temporaryRoot, {force: true, recursive: true});
  }
});

test('findings JSON path is explicit and limited to the complete matrix', () => {
  const options = parseArguments(['--findings-json', 'reports/browser-findings.json']);
  assert.ok(options.findingsJsonPath.endsWith('/reports/browser-findings.json'));
  assert.throws(
    () => parseArguments(['--findings-json', 'findings.json', '--waterfall-only']),
    /requires the complete dashboard matrix audit/,
  );
  assert.throws(
    () => parseArguments(['--findings-json', 'findings.json', '--list']),
    /requires the complete dashboard matrix audit/,
  );
});

test('cold Chrome readiness may pass after the operation deadline without weakening it', async () => {
  let now = 0;
  const response = {ok: true};
  const actual = await waitForHttp(url, 90000, {exitCode: null, signalCode: null}, {
    now: () => now,
    fetcher: async () => {
      if (now <= 30000) {
        throw Object.assign(new Error('fixture cold start'), {code: 'ECONNREFUSED'});
      }
      return response;
    },
    sleep: async ms => { now += ms; },
  });
  assert.equal(actual, response);
  assert.equal(now, 30100);
  assert.equal(parseArguments(['--timeout-ms', '30000']).timeoutMs, 30000);
});

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

test('a signal-terminated child remains a failure without making an HTTP request', async () => {
  await assert.rejects(waitForHttp(url, 30000, {exitCode: null, signalCode: 'SIGTERM'}, {
    now: () => 0,
    fetcher: async () => assert.fail('Child already exited from a signal'),
  }), error => {
    assert.match(error.message, /process exited before/);
    assert.equal(JSON.parse(error.message.split('; readiness=')[1]).signalCode, 'SIGTERM');
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

function fakeChild({exitCode = null, signalCode = null, onKill = () => true} = {}) {
  const child = Object.assign(new EventEmitter(), {exitCode, signalCode, kills: []});
  child.kill = signal => {
    child.kills.push(signal);
    return onKill(child, signal);
  };
  return child;
}

test('child cleanup is a no-op after normal or signal termination', async () => {
  const exited = fakeChild({exitCode: 0});
  const signaled = fakeChild({signalCode: 'SIGTERM'});
  await stopChild(exited, {termTimeoutMs: 5, killTimeoutMs: 5});
  await stopChild(signaled, {termTimeoutMs: 5, killTimeoutMs: 5});
  assert.deepEqual(exited.kills, []);
  assert.deepEqual(signaled.kills, []);
  assert.equal(exited.listenerCount('exit'), 0);
  assert.equal(signaled.listenerCount('exit'), 0);
});

test('child cleanup observes a synchronous SIGTERM exit', async () => {
  const child = fakeChild({onKill: (fixture, signal) => {
    fixture.signalCode = signal;
    fixture.emit('exit', null, signal);
    return true;
  }});
  await stopChild(child, {termTimeoutMs: 50, killTimeoutMs: 50});
  assert.deepEqual(child.kills, ['SIGTERM']);
  assert.equal(child.listenerCount('exit'), 0);
});

test('child cleanup clears the graceful waiter after an asynchronous exit', async () => {
  const child = fakeChild({onKill: (fixture, signal) => {
    setImmediate(() => {
      fixture.signalCode = signal;
      fixture.emit('exit', null, signal);
    });
    return true;
  }});
  await stopChild(child, {termTimeoutMs: 50, killTimeoutMs: 50});
  assert.deepEqual(child.kills, ['SIGTERM']);
  assert.equal(child.listenerCount('exit'), 0);
});

test('child cleanup installs the second waiter before synchronous SIGKILL exit', async () => {
  const child = fakeChild({onKill: (fixture, signal) => {
    if (signal === 'SIGKILL') {
      fixture.signalCode = signal;
      fixture.emit('exit', null, signal);
    }
    return true;
  }});
  await stopChild(child, {termTimeoutMs: 5, killTimeoutMs: 50});
  assert.deepEqual(child.kills, ['SIGTERM', 'SIGKILL']);
  assert.equal(child.listenerCount('exit'), 0);
});

test('child cleanup rejects within both bounded waits when exit is never observed', async () => {
  const child = fakeChild();
  await assert.rejects(
    stopChild(child, {termTimeoutMs: 5, killTimeoutMs: 5}),
    /child did not exit after SIGTERM \(5ms\) and SIGKILL \(5ms\)/,
  );
  assert.deepEqual(child.kills, ['SIGTERM', 'SIGKILL']);
  assert.equal(child.listenerCount('exit'), 0);
});

test('concurrent child cleanup callers share one signal sequence', async () => {
  const child = fakeChild({onKill: (fixture, signal) => {
    setImmediate(() => {
      fixture.signalCode = signal;
      fixture.emit('exit', null, signal);
    });
    return true;
  }});
  const first = stopChild(child, {termTimeoutMs: 50, killTimeoutMs: 50});
  const second = stopChild(child, {termTimeoutMs: 50, killTimeoutMs: 50});
  assert.equal(first, second);
  await Promise.all([first, second]);
  assert.deepEqual(child.kills, ['SIGTERM']);
  assert.equal(child.listenerCount('exit'), 0);
  await stopChild(child, {termTimeoutMs: 5, killTimeoutMs: 5});
  assert.deepEqual(child.kills, ['SIGTERM']);
});
