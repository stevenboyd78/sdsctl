from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from sds200 import scanner_display_admin as admin_module
from sds200 import scanner_display_upload as upload_module
from sds200.scanner_display_admin import DisplayAdminConflict, ScannerDisplayProfileAdmin
from sds200.scanner_display_configuration import load_scanner_display_configuration
from sds200.scanner_display_ingress import (
    DISPLAY_PROFILE_ADMIN_PATH as ROUTE,
)
from sds200.scanner_display_ingress import (
    ScannerDisplayIngress,
    ScannerDisplayIngressMiddleware,
    _body,
)
from sds200.scanner_display_profile import MAX_PROFILE_BYTES
from sds200.scanner_display_profile_storage import (
    DisplayProfileStorageError,
    initialize_display_profile_storage,
)
from sds200.scanner_display_upload import DisplayUploadUnconfirmed, ScannerDisplayUpload
from sds200.web_dashboard import create_web_dashboard_app

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX managed profile upload")
UID, OTHER_UID = "a" * 32, "b" * 32
ORIGIN, PEER = "https://ha.example.test", ("172.30.32.2", 1234)
SOURCE = (
    b"Owner\tPRIVATE_SENTINEL\r\n"
    b"DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\tOff\tAFS\tCOLOR\r\n"
    b"DispOptItems\tDispOptId=2\tDispLayoutId=1\tFrequency\tEmpty\r\n"
    b"DispColors\tDispColorId=2\tColorLayoutId=1\tffffff\t000000\r\n"
)
OTHER = SOURCE.replace(b"ffffff", b"123456")


def forbidden(*args, **kwargs):
    pytest.fail("Must not create a scanner connection or use normal dashboard clients")


@pytest.fixture
def configured(tmp_path):
    managed = tmp_path / "managed"
    managed.mkdir(mode=0o700)
    path = tmp_path / "display.toml"
    values = {
        "version": 1,
        "endpoint_id": str(UUID(int=1)),
        "source_id": str(UUID(int=2)),
        "scanner_target": "udp://192.0.2.25:50536",
        "source_path": str(managed / "profile.cfg"),
        "state_directory": str(tmp_path / "accepted"),
    }
    path.write_text("\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()))
    path.chmod(0o600)
    config = load_scanner_display_configuration(path)
    config.source_path.write_bytes(SOURCE)
    config.source_path.chmod(0o600)
    initialize_display_profile_storage(config.state_directory, config.binding.endpoint_id)
    repository = config.repository()
    preview = repository.prepare(config.binding, acquired_at=datetime.now(UTC))
    repository.commit(preview, imported_at=datetime.now(UTC))
    return path, config


def state(config):
    return (config.state_directory / "accepted-profile.json").read_bytes()


def prepare(upload, data=OTHER):
    return upload.prepare(data, acquired_at=datetime.now(UTC))


def commit(upload, preview):
    return upload.commit(preview, imported_at=datetime.now(UTC))


def test_upload_preview_cancel_is_inert_then_replacement_is_exact_and_private(configured):
    _, config = configured
    upload = ScannerDisplayUpload(config)
    before = state(config), config.source_path.stat()
    preview = prepare(upload)
    assert "PRIVATE_SENTINEL" not in repr(preview)
    assert (state(config), config.source_path.stat()) == before
    upload.cancel(preview)
    with pytest.raises(DisplayProfileStorageError):
        commit(upload, preview)
    accepted = commit(upload, prepare(upload))
    assert config.source_path.read_bytes() == OTHER
    assert config.source_path.stat().st_mode & 0o777 == 0o600
    restored = config.repository().inspect()
    assert restored.profile.last_good.profile.revision == accepted.profile.revision
    assert restored.source_status == "matches_import"
    assert list(config.source_path.parent.iterdir()) == [config.source_path]


@pytest.mark.parametrize("data", [b"", b"invalid", b"x" * (MAX_PROFILE_BYTES + 1), "text", None])
def test_upload_invalid_never_changes_source_or_accepted(configured, data):
    _, config = configured
    before = state(config)
    with pytest.raises(DisplayProfileStorageError):
        prepare(ScannerDisplayUpload(config), data)
    assert state(config) == before
    assert config.source_path.read_bytes() == SOURCE


def test_upload_can_create_missing_managed_copy(configured):
    _, config = configured
    config.source_path.unlink()
    upload = ScannerDisplayUpload(config)
    commit(upload, prepare(upload))
    assert config.source_path.read_bytes() == OTHER


@pytest.mark.parametrize("kind", ["source-edit", "source-replaced", "state-edit", "other-import"])
def test_upload_refuses_changes_between_review_and_acceptance(configured, kind):
    _, config = configured
    upload = ScannerDisplayUpload(config)
    preview = prepare(upload)
    if kind == "source-edit":
        config.source_path.write_bytes(SOURCE + b"Extension\tchanged\r\n")
    elif kind == "source-replaced":
        config.source_path.unlink()
        config.source_path.write_bytes(SOURCE)
        config.source_path.chmod(0o600)
    elif kind == "state-edit":
        (config.state_directory / "accepted-profile.json").write_bytes(b"invalid PRIVATE_SENTINEL")
    else:
        repo = config.repository()
        review = repo.prepare(config.binding, acquired_at=datetime.now(UTC))
        repo.commit(review, imported_at=datetime.now(UTC))
    before = state(config), config.source_path.read_bytes()
    with pytest.raises(DisplayProfileStorageError):
        commit(upload, preview)
    assert (state(config), config.source_path.read_bytes()) == before
    with pytest.raises(DisplayProfileStorageError):
        commit(upload, preview)


@pytest.mark.parametrize(
    "kind", ["public-parent", "readonly-source", "public-source", "symlink", "hardlink", "fifo"]
)
def test_upload_never_repairs_unsafe_or_readonly_paths(configured, kind):
    _, config = configured
    if kind == "public-parent":
        config.source_path.parent.chmod(0o755)
    elif kind == "readonly-source":
        config.source_path.chmod(0o400)
    elif kind == "public-source":
        config.source_path.chmod(0o644)
    elif kind == "hardlink":
        os.link(config.source_path, config.source_path.with_suffix(".keep"))
    else:
        kept = config.source_path.with_suffix(".keep")
        config.source_path.rename(kept)
        if kind == "symlink":
            config.source_path.symlink_to(kept)
        else:
            os.mkfifo(config.source_path)
    before = state(config)
    with pytest.raises(DisplayProfileStorageError):
        prepare(ScannerDisplayUpload(config))
    assert state(config) == before


def test_source_identity_confirmation_happens_before_any_upload_write(configured):
    _, config = configured
    changed = replace(config, binding=replace(config.binding, source_id=UUID(int=3)))
    upload = ScannerDisplayUpload(changed)
    before = state(config)
    with pytest.raises(DisplayProfileStorageError):
        commit(upload, prepare(upload))
    assert state(config) == before and config.source_path.read_bytes() == SOURCE
    upload.commit(prepare(upload), imported_at=datetime.now(UTC), confirm_source_change=True)
    assert config.source_path.read_bytes() == OTHER


def test_state_write_failure_after_source_replacement_retains_last_good(configured, monkeypatch):
    _, config = configured
    before = state(config)
    upload = ScannerDisplayUpload(config)
    preview = prepare(upload)
    monkeypatch.setattr(
        upload_module,
        "_publish",
        lambda *a, **k: (_ for _ in ()).throw(OSError("PRIVATE_SENTINEL")),
    )
    with pytest.raises(DisplayUploadUnconfirmed) as error:
        commit(upload, preview)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert state(config) == before and config.source_path.read_bytes() == OTHER
    assert config.repository().inspect().source_status == "changed_since_import"
    with pytest.raises(DisplayProfileStorageError):
        commit(upload, preview)


def test_source_post_rename_failure_is_unconfirmed(configured, monkeypatch):
    _, config = configured
    before = state(config)
    upload = ScannerDisplayUpload(config)
    preview = prepare(upload)
    fsync = os.fsync
    count = 0

    def fail_directory(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("PRIVATE_SENTINEL")
        return fsync(fd)

    monkeypatch.setattr(upload_module.os, "fsync", fail_directory)
    with pytest.raises(DisplayUploadUnconfirmed):
        commit(upload, preview)
    assert state(config) == before and config.source_path.read_bytes() == OTHER


def test_abrupt_exit_between_source_and_accepted_publication(configured):
    path, config = configured
    before = state(config)
    script = """
import os, sys
from pathlib import Path
from datetime import UTC, datetime
from sds200.scanner_display_configuration import load_scanner_display_configuration
import sds200.scanner_display_upload as m
c = load_scanner_display_configuration(Path(sys.argv[1]))
u = m.ScannerDisplayUpload(c)
data = c.source_path.read_bytes().replace(b'ffffff', b'123456')
p = u.prepare(data, acquired_at=datetime.now(UTC))
m._publish = lambda *args, **kwargs: os._exit(73)
u.commit(p, imported_at=datetime.now(UTC))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)], capture_output=True, timeout=10
    )
    assert result.returncode == 73, result.stderr.decode()
    assert state(config) == before and config.source_path.read_bytes() == OTHER
    assert config.repository().inspect().profile.last_good is not None


@pytest.fixture
def admin(configured, tmp_path):
    _, config = configured
    return ScannerDisplayProfileAdmin(
        config,
        recording_directory=tmp_path / "recordings",
        daemon_socket_path=tmp_path / "absent.sock",
        allow_upload=True,
    )


def test_admin_review_is_user_bound_one_use_and_reload_failure_is_distinct(admin, configured):
    review = admin.preview(UID, OTHER)
    with pytest.raises(DisplayAdminConflict):
        admin.preview(OTHER_UID, OTHER)
    with pytest.raises(DisplayAdminConflict):
        admin.commit(OTHER_UID, review["review_id"])
    result = admin.commit(UID, review["review_id"])
    assert result["status"] == "accepted" and result["daemon_reload"] == "unconfirmed"
    assert configured[1].source_path.read_bytes() == OTHER
    with pytest.raises(DisplayAdminConflict):
        admin.commit(UID, review["review_id"])


def test_admin_status_separates_configured_source_from_accepted_provenance(admin, configured):
    result = admin.status()
    accepted = result["accepted"]

    assert result["configured_source"] == {
        "source_id": str(configured[1].binding.source_id),
        "source_kind": "manual_import",
        "path": str(configured[1].source_path),
    }
    assert result["source_status"] == "matches_import"
    assert result["source_path"] == str(configured[1].source_path)
    assert accepted["source_kind"] == "manual_import"
    assert datetime.fromisoformat(accepted["acquired_at"]) <= datetime.fromisoformat(
        accepted["imported_at"]
    )
    assert datetime.fromisoformat(result["status_checked_at"]).tzinfo is not None


def test_admin_expiry_and_close_discard_without_writes(admin, configured):
    before = state(configured[1])
    moment = [0]
    admin._clock = lambda: moment[0]
    review = admin.preview(UID, OTHER)
    moment[0] = 301
    with pytest.raises(DisplayAdminConflict):
        admin.commit(UID, review["review_id"])
    admin.preview(UID, OTHER)
    admin.close()
    assert state(configured[1]) == before
    assert configured[1].source_path.read_bytes() == SOURCE


@pytest.fixture
def app(admin):
    config = ScannerDisplayIngress(admin, ORIGIN, frozenset({UID, OTHER_UID}))
    return create_web_dashboard_app(
        forbidden, home_assistant_ingress=True, scanner_display_admin_ingress=config
    )


@pytest.fixture
def client(app):
    with TestClient(app, client=PEER, headers={"X-Remote-User-Id": UID}) as client:
        yield client


def csrf(client):
    result = client.get(ROUTE)
    assert result.status_code == 200
    assert "Cache-Control" in result.headers
    return re.search(r'name="profile-csrf" content="([a-f0-9]{64})"', result.text).group(1)


def post(client, action, data=None, token=None, **kwargs):
    headers = {
        "Origin": ORIGIN,
        "X-SDSCTL-Profile-CSRF": token or csrf(client),
        "Content-Type": "application/octet-stream" if action == "upload" else "application/json",
    }
    content = data if action == "upload" else json.dumps({} if data is None else data)
    return client.post(ROUTE + "/" + action, content=content, headers=headers, **kwargs)


def test_default_factory_does_not_expose_profile_admin(admin):
    for ingress in (False, True):
        app = create_web_dashboard_app(forbidden, home_assistant_ingress=ingress)
        with TestClient(app, client=PEER) as c:
            assert c.get(ROUTE).status_code == 404
            assert "home_assistant_scanner_display_profile" not in c.get("/api/v1").json()["links"]
    with pytest.raises(ValueError):
        create_web_dashboard_app(
            forbidden,
            scanner_display_admin_ingress=ScannerDisplayIngress(admin, ORIGIN, frozenset({UID})),
        )


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("X-Remote-User-Id", "c" * 32)],
        [("X-Remote-User-Id", UID), ("X-Remote-User-Id", UID)],
        [("Cookie", "__Host-sdsctl-session=operator")],
        [("Cookie", "__Host-sdsctl-device-session=display")],
        [("X-Remote-User-Name", "admin")],
    ],
)
def test_admin_identity_missing_unlisted_duplicate_and_cookies_denied(app, configured, headers):
    before = state(configured[1])
    with TestClient(app, client=PEER) as c:
        for suffix, method in [
            ("", "GET"),
            ("/status", "GET"),
            ("/upload", "POST"),
            ("/commit", "POST"),
        ]:
            assert c.request(method, ROUTE + suffix, headers=headers).status_code == 403
    assert state(configured[1]) == before


def test_forged_proxy_peer_does_not_authorize(app):
    with TestClient(app, client=("192.0.2.12", 1234)) as c:
        assert (
            c.get(ROUTE, headers={"X-Remote-User-Id": UID, "X-Forwarded-For": PEER[0]}).status_code
            == 403
        )


@pytest.mark.parametrize(
    "origin",
    ["http://ha.example.test", "https://ha.example.test/", "https://user:pass@ha.example.test"],
)
def test_private_ingress_origin_validation(admin, origin):
    with pytest.raises(ValueError):
        ScannerDisplayIngress(admin, origin, frozenset({UID}))


@pytest.mark.parametrize(
    "users", [frozenset(), {UID}, frozenset({True}), frozenset({"administrator"})]
)
def test_private_ingress_allowlist_validation(admin, users):
    with pytest.raises(ValueError):
        ScannerDisplayIngress(admin, ORIGIN, users)


def test_page_is_no_store_nonce_csp_and_never_contains_raw_profile(client):
    page = client.get(ROUTE)
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    nonce = re.search(r'<script nonce="([a-f0-9]{64})"', page.text).group(1)
    assert f"script-src 'nonce-{nonce}'" in page.headers["content-security-policy"]
    assert "__CSRF_TOKEN__" not in page.text and "__CSP_NONCE__" not in page.text
    assert "PRIVATE_SENTINEL" not in page.text
    assert "source_path" in client.get(ROUTE + "/status").json()


def test_http_upload_review_cancel_refresh_commit_and_no_raw_projection(client, configured):
    before = state(configured[1])
    review = post(client, "upload", OTHER).json()
    assert "PRIVATE_SENTINEL" not in json.dumps(review)
    assert state(configured[1]) == before and configured[1].source_path.read_bytes() == SOURCE
    cancelled = post(client, "cancel", {"review_id": review["review_id"]}, token=review["csrf"])
    assert cancelled.json()["status"] == "cancelled"
    review = post(client, "refresh").json()
    result = post(
        client,
        "commit",
        {"review_id": review["review_id"], "confirm_source_change": False},
        token=review["csrf"],
    )
    assert result.status_code == 200 and result.json()["status"] == "accepted"
    assert result.json()["daemon_reload"] == "unconfirmed"
    assert configured[1].source_path.read_bytes() == SOURCE
    assert "PRIVATE_SENTINEL" not in result.text


def test_http_upload_commit_and_exact_nonce_replay_denied(client, configured):
    review = post(client, "upload", OTHER).json()
    payload = {"review_id": review["review_id"], "confirm_source_change": False}
    assert post(client, "commit", payload, token=review["csrf"]).status_code == 200
    saved = state(configured[1])
    assert post(client, "commit", payload, token=review["csrf"]).status_code == 400
    assert post(client, "commit", payload).status_code == 409
    assert state(configured[1]) == saved and configured[1].source_path.read_bytes() == OTHER


@pytest.mark.parametrize(
    "change", ["origin", "query", "fetch-site", "duplicate-csrf", "authorization", "encoding"]
)
def test_http_untrusted_context_denied_before_preview(client, admin, monkeypatch, change):
    token = csrf(client)
    monkeypatch.setattr(admin, "preview", forbidden)
    headers = [
        ("Origin", ORIGIN),
        ("X-SDSCTL-Profile-CSRF", token),
        ("Content-Type", "application/json"),
    ]
    path = ROUTE + "/refresh"
    if change == "origin":
        headers[0] = ("Origin", "https://evil.example")
    elif change == "query":
        path += "?path=/tmp/elsewhere"
    elif change == "fetch-site":
        headers.append(("Sec-Fetch-Site", "cross-site"))
    elif change == "duplicate-csrf":
        headers.append(("X-SDSCTL-Profile-CSRF", token))
    elif change == "authorization":
        headers.append(("Authorization", "Bearer PRIVATE_SENTINEL"))
    else:
        headers.append(("Content-Encoding", "gzip"))
    assert client.post(path, content="{}", headers=headers).status_code in {400, 403}


@pytest.mark.parametrize(
    "payload",
    [b'{"review_id":"a","review_id":"b"}', b'{"path":"/tmp/evil"}', b"[]", b"{", b"[" * 1500],
)
def test_malformed_or_path_parameters_never_mutate(client, admin, monkeypatch, payload):
    token = csrf(client)
    monkeypatch.setattr(admin, "commit", forbidden)
    response = client.post(
        ROUTE + "/commit",
        content=payload,
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/json",
            "X-SDSCTL-Profile-CSRF": token,
        },
    )
    assert response.status_code == 400


def test_oversize_upload_rejected_without_controller(client, admin, monkeypatch):
    token = csrf(client)
    monkeypatch.setattr(admin, "preview", forbidden)
    assert post(client, "upload", b"x" * (MAX_PROFILE_BYTES + 1), token=token).status_code == 413


def test_stream_body_limit_disconnect_and_exact_bytes():
    async def receive_messages(messages):
        async def receive():
            return messages.pop(0)

        return await _body(receive, 4)

    assert (
        asyncio.run(
            receive_messages(
                [
                    {"type": "http.request", "body": b"ab", "more_body": True},
                    {"type": "http.request", "body": b"cd"},
                ]
            )
        )
        == b"abcd"
    )
    for messages in ([{"type": "http.request", "body": b"12345"}], [{"type": "http.disconnect"}]):
        with pytest.raises(ValueError):
            asyncio.run(receive_messages(messages))


def test_lifespan_discards_pending_upload_without_save(app, configured):
    before = state(configured[1])
    with TestClient(app, client=PEER, headers={"X-Remote-User-Id": UID}) as c:
        assert post(c, "upload", OTHER).status_code == 200
    assert state(configured[1]) == before and configured[1].source_path.read_bytes() == SOURCE


def test_successful_reload_uses_explicit_local_identity_guard(admin, monkeypatch):
    calls = []

    def reload(configuration, path):
        calls.append((configuration, path))
        return {
            "accepted": {
                "revision": configuration.repository().inspect().profile.last_good.profile.revision
            }
        }

    monkeypatch.setattr(admin_module, "_reload", reload)
    review = admin.preview(UID, OTHER)
    result = admin.commit(UID, review["review_id"])
    assert result["daemon_reload"] == "confirmed"
    assert calls == [(admin.configuration, admin._socket)]


def test_reload_of_other_revision_is_not_reported_as_this_review_confirmed(admin, monkeypatch):
    monkeypatch.setattr(admin_module, "_reload", lambda *a: {"accepted": {"revision": "different"}})
    review = admin.preview(UID, OTHER)
    result = admin.commit(UID, review["review_id"])
    assert result["status"] == "accepted"
    assert result["daemon_reload"] == "different_revision"


def test_http_upload_disabled_before_reading_body(client, admin, monkeypatch):
    import sds200.scanner_display_ingress as module

    token = csrf(client)
    admin.allow_upload = False
    monkeypatch.setattr(module, "_body", forbidden)
    assert post(client, "upload", OTHER, token=token).status_code == 403


def test_csrf_is_bound_to_exact_admin_identity(client, admin, monkeypatch):
    token = csrf(client)
    monkeypatch.setattr(admin, "preview", forbidden)
    response = client.post(
        ROUTE + "/refresh",
        content="{}",
        headers={
            "X-Remote-User-Id": OTHER_UID,
            "Origin": ORIGIN,
            "Content-Type": "application/json",
            "X-SDSCTL-Profile-CSRF": token,
        },
    )
    assert response.status_code == 400


def test_nonce_capacity_refuses_more_pages_without_creating_state(client, configured):
    before = state(configured[1])
    for _ in range(32):
        assert client.get(ROUTE).status_code == 200
    assert client.get(ROUTE).status_code == 503
    assert state(configured[1]) == before


def test_missing_lifespan_refuses_admin_and_websocket_is_closed(admin):
    async def next_app(*args):
        pytest.fail("Profile request must not fall through")

    middleware = ScannerDisplayIngressMiddleware(
        next_app, configuration=ScannerDisplayIngress(admin, ORIGIN, frozenset({UID}))
    )

    async def run(kind):
        messages = []

        async def send(message):
            messages.append(message)

        await middleware(
            {
                "type": kind,
                "path": ROUTE,
                "client": PEER,
                "headers": [(b"x-remote-user-id", UID.encode())],
            },
            forbidden,
            send,
        )
        return messages

    assert asyncio.run(run("http"))[0]["status"] == 503
    assert asyncio.run(run("websocket"))[0] == {"type": "websocket.close", "code": 1008}
    middleware._workers.close()


def test_cancelled_http_commit_is_not_rolled_back_or_replayed(admin, configured, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def reload(configuration, path):
        calls.append(path)
        started.set()
        assert release.wait(5)
        return {
            "accepted": {
                "revision": configuration.repository().inspect().profile.last_good.profile.revision
            }
        }

    monkeypatch.setattr(admin_module, "_reload", reload)
    review = admin.preview(UID, OTHER)

    async def next_app(*args):
        pytest.fail("No next application call")

    middleware = ScannerDisplayIngressMiddleware(
        next_app, configuration=ScannerDisplayIngress(admin, ORIGIN, frozenset({UID}))
    )
    middleware._ready = True
    token = middleware._nonce(UID)
    data = json.dumps({"review_id": review["review_id"], "confirm_source_change": False}).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": ROUTE + "/commit",
        "client": PEER,
        "headers": [
            (b"x-remote-user-id", UID.encode()),
            (b"origin", ORIGIN.encode()),
            (b"content-type", b"application/json"),
            (b"x-sdsctl-profile-csrf", token.encode()),
        ],
    }

    async def exercise():
        async def receive():
            return {"type": "http.request", "body": data}

        async def send(message):
            pytest.fail("Cancelled response must not be sent")

        task = asyncio.create_task(middleware(scope, receive, send))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.to_thread(admin.close)

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        middleware._workers.close()
    assert len(calls) == 1
    assert configured[1].source_path.read_bytes() == OTHER
    assert configured[1].repository().inspect().source_status == "matches_import"
    assert token not in middleware._nonces


def test_recording_overlap_prevents_admin_creation(configured, tmp_path):
    with pytest.raises(ValueError):
        ScannerDisplayProfileAdmin(
            configured[1], recording_directory=tmp_path, daemon_socket_path=tmp_path / "daemon.sock"
        )


def test_unexpected_error_is_sanitized_and_never_echoed(client, admin, monkeypatch):
    token = csrf(client)

    def fail(*args):
        raise RuntimeError("PRIVATE_SENTINEL")

    monkeypatch.setattr(admin, "preview", fail)
    response = post(client, "refresh", token=token)
    assert response.status_code == 500
    assert "PRIVATE_SENTINEL" not in response.text
    assert response.headers["cache-control"] == "no-store"
