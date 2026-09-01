# Operations and Diagnostics

This page is for a working installation that needs persistent configuration,
health inspection, recovery, logs, or a daemon. Complete
[First connection](First-Connection) before changing these settings.

## Layered configuration

Optional application settings load in this precedence order:

1. `/etc/sdsctl/config.toml`
2. `${XDG_CONFIG_HOME:-~/.config}/sdsctl/config.toml`
3. Supported `SDSCTL_*` environment variables
4. Explicit command-line options

Missing files preserve defaults and are not created automatically. The older
connection-profile file remains separate. Read the
[configuration guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/configuration.md)
for the versioned schema and exact field provenance.

## Health and events

```bash
sdsctl --profile home health
sdsctl --profile home health --watch 5 --history
sdsctl --profile home events --json
```

Use JSON output for scripts, not for presenting unsanitized scanner or local
environment data publicly.

## Logging and traffic capture

```bash
sdsctl --log-level INFO --profile home monitor
sdsctl --log-level DEBUG --log-file /var/log/sdsctl.log \
  --profile home events
```

`-v` selects `INFO`, `-vv` selects `DEBUG`, and an explicit `--log-level`
wins. Raw scanner traffic is separate under `--trace`. See
[operational logging](https://github.com/stevenboyd78/sdsctl/blob/main/docs/logging.md)
for service ownership, journald, permissions, and rotation.

Capture and replay support hardware-independent diagnosis:

```bash
sdsctl --model SDS100 --capture sds100-info.jsonl info
sdsctl --replay sds100-info.jsonl --model SDS100 info
```

Captures can contain scanner and local data. Use repeated `--redact TEXT`
options and inspect the complete file before sharing it. Read
[capture and replay](https://github.com/stevenboyd78/sdsctl/blob/main/docs/replay-and-capture.md).

## Foreground daemon

```bash
sdsctl --log-level INFO --host SCANNER_IP daemon
```

The daemon owns scanner control, PSI, one SDS200 RTSP/RTP session, decoded-audio
routing, recordings, and private local client sockets. It does not fork or
expose an unrestricted TCP daemon API. Stop an interactive run with `Ctrl+C`;
use the [daemon deployment guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/daemon-deployment.md)
before installing a persistent service.

Representative read-only client checks are:

```bash
sdsctl daemon-client status --json
sdsctl daemon-client snapshot
sdsctl daemon-client events --count 10 --json
sdsctl daemon-client waterfall --duration 10 --count 100 --json
```

## Profiles and recovery

Repair a stale saved endpoint without changing its preferred transport:

```bash
sdsctl profile repair home --network 192.168.1.0/24 --dry-run
sdsctl profile repair home --network 192.168.1.0/24
```

Review the dry run first. Preferred recovery and automatic PSI recovery are
bounded but opt-in or configurable behaviors; read the
[reliability guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/reliability.md)
and [fallback guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/fallback-profiles.md)
before changing their defaults.

## Troubleshooting and undo

- Stop an interactive monitor, TUI, web service, or daemon with `Ctrl+C`.
- Revert temporary CLI overrides by omitting them on the next command.
- Review a configuration-file backup before restoring it.
- Do not delete runtime, profile, recording, or Favorites directories as a
  generic repair step.
- Use [Troubleshooting](Troubleshooting) before collecting a detailed report.
