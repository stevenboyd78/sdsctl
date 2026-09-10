"""Fixture-only real Chromium reader after synthetic pending-state recovery.

This uses the installed native helper unchanged, not a simulated browser parent.
The test launcher is deliberately NOT a product continuation-start command. The
ordinary CLI must remain blocked, and no online review or sign-in is attempted.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from sds200.browser_device_continuation_activation import BrowserPausedActivation
from sds200.browser_device_continuation_current import inspect_stopped_continuation
from sds200.browser_device_continuation_history import inspect_continuation_history
from sds200.browser_device_continuation_intent import BrowserContinuationIntent
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_startup import BrowserStartupError, _launch_lock, check_browser_startup
from sds200.browser_device_worker import worker_graph


def fingerprint(roots):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in roots for p in root.rglob("*") if p.is_file()}


def qualify(handoff, released, startup_args, command, stage, x, wait, put, emit,
            no_connections):
    """Explicit fixture consent, two actual offline reader starts, no state repair."""
    recorded = BrowserContinuationIntent(handoff, release_id=released.release_id).apply(
        confirmation=lambda review: review.confirmation)
    activation = BrowserPausedActivation(handoff, release_id=released.release_id,
                                        intent_id=recorded.intent_id).apply(
        confirmation=lambda review: review.confirmation)
    assert activation.mode is RecoveryMode.PAUSED
    selected = {key: startup_args[key] for key in ("bundle", "profile", "public_key")}
    root = startup_args["root"]
    before = inspect_stopped_continuation(root, **selected)
    assert before.epoch == activation.epoch and before.mode is RecoveryMode.PAUSED
    roots = (selected["profile"], selected["bundle"], handoff._session._archives)
    frozen = fingerprint(roots)
    history = inspect_continuation_history(handoff, release_id=released.release_id,
                                          intent_id=recorded.intent_id)

    def guarded():
        try:
            check_browser_startup(**startup_args)
        except BrowserStartupError:
            return
        raise AssertionError("Ordinary startup adopted a continuation profile")

    guarded()
    no_connections()
    for label in ("continuation-reader-start", "continuation-reader-restart"):
        # Same fixed managed argv, same generated bundle/native helper. Only the
        # fictional launcher holds the actual existing launch-lock inode here.
        # No role/path is supplied in a browser message or native stdin envelope.
        with _launch_lock(root, create=False):
            playwright = os.environ.get("SDSCTL_READER_PLAYWRIGHT")
            active_port = root / "DevToolsActivePort"
            prior = active_port.stat().st_mtime_ns if active_port.exists() else None
            launch = ((command[0], "--remote-debugging-port=0", *command[1:])
                      if playwright else command)
            process = subprocess.Popen(launch, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                if playwright:
                    # Optional diagnostic run, NOT an uninstrumented UI pass.
                    assert Path(playwright).is_absolute()

                    def new_port(child=process, original=prior, marker=active_port):
                        assert child.poll() is None
                        return marker.exists() and marker.stat().st_mtime_ns != original

                    wait(new_port, 15)
                    port = active_port.read_text().splitlines()[0]
                    extension_id = command[-1].split("/")[2]
                    inspected = subprocess.run(["node", str(Path(__file__).with_name(
                        "diagnose_browser_continuation_read.mjs")), playwright, port,
                        extension_id, worker_graph()[0]], capture_output=True, text=True,
                        timeout=35, check=True)
                    report = json.loads(inspected.stdout)
                    put(stage / (label + "-diagnostic.json"), json.dumps(report))
                    emit("actual-reader-context-diagnostic", **report)

                def window(child=process):
                    assert child.poll() is None, "Fictional reader exited"
                    no_connections()
                    return next((wid for wid, title in x.windows()
                                 if "SDSCTL managed display startup" in title), None)

                selected_window = wait(window, 45)

                def paused(child=process, target=selected_window):
                    assert child.poll() is None
                    no_connections()
                    text = x.text(target)
                    return text if ("Automatic sign-in is paused. Ask your administrator"
                                    in text) else None

                text = wait(paused, 60)
                put(stage / (label + ".txt"), text)
                x.screenshot(stage / (label + ".png"))
                time.sleep(6)  # Observe a second ordinary five-second UI read.
                assert paused()
                assert fingerprint(roots) == frozen
            except BaseException:
                # Capture while the private browser still exists. The outer
                # ledger reporter cannot recover this display after shutdown.
                emit("reader-failed-private-display-titles",
                     titles=[title for _, title in x.windows()])
                x.screenshot(stage / (label + "-failed.png"))
                raise
            finally:
                # Match ordinary product shutdown: SIGINT asks Chromium to
                # flush the profile and remove its own Singleton markers.
                # SIGTERM can leave them behind even after a zero exit. Never
                # remove markers ourselves or accept forced cleanup as a pass.
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
                    raise AssertionError("Fictional reader required forced cleanup") from None
        guarded()
        assert inspect_stopped_continuation(root, **selected) == before
        assert inspect_continuation_history(handoff, release_id=released.release_id,
                                            intent_id=recorded.intent_id) == history
        assert fingerprint(roots) == frozen
        no_connections()
        emit(label, actual_native_reader=True, paused=True, authentication_connections=0,
             manual_reload=False, product_continuation_launcher=False,
             cdp_diagnostic=bool(playwright))
    return frozen
