# Using sdsctl

Start with [First connection](First-Connection). The interfaces below share the
same scanner and daemon contracts, but they are intended for different jobs.

## Command line

Use the command line for discovery, one operation, scripting, or diagnostics:

```bash
sdsctl discover
sdsctl info
sdsctl monitor
sdsctl capabilities
sdsctl health
```

Add `--host SCANNER_IP`, `--port DEVICE`, or `--profile NAME` before the action
to select a connection. Run `sdsctl --help` and `sdsctl ACTION --help` for the
complete current option list.

## Terminal interface

Install and launch the full-screen Textual interface:

```bash
python -m pip install "sds200[tui]"
sdsctl tui
```

Press `?` for the keyboard reference and `Q` to quit. Add the `playback` extra
for live and saved audio on a workstation:

```bash
python -m pip install "sds200[tui,playback]"
```

The [Textual TUI guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/tui.md)
covers layouts, controls, themes, logging, daemon-client mode, audio, and
recordings.

## Web dashboard

The web dashboard is a client of one foreground daemon. Start the daemon in one
terminal and the loopback web service in another:

```bash
python -m pip install "sds200[web]"
sdsctl --host SCANNER_IP daemon
sdsctl web
```

Open `http://127.0.0.1:8000/` on the same computer. The default listener is
loopback-only. Do not publish it to a LAN by changing the bind address; use the
documented authenticated native-TLS mode when remote LAN access is required.

See [Web Dashboard](Web-Dashboard) for the visual workspace and the
[canonical web guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/web-dashboard.md)
for its security and deployment contracts.

## Daemon and MQTT

Use the foreground daemon when one process should own scanner control, PSI,
audio, recordings, and local clients:

```bash
python -m pip install "sds200[mqtt,web]"
sdsctl --host SCANNER_IP daemon
sdsctl daemon-client status
sdsctl daemon-client snapshot
sdsctl tui --daemon-client
```

The daemon remains in the foreground for a service manager. Stop it with
`Ctrl+C` during an interactive test. Read the
[daemon deployment guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/daemon-deployment.md),
[daemon API guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/daemon-api.md),
and [MQTT guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/daemon-mqtt.md)
before enabling a persistent service or commands.

## Scanner controls

Prefer typed or semantic controls over raw protocol commands. Representative
standalone commands include:

```bash
sdsctl hold SYS 100
sdsctl next DEPT 200 100 --count 2
sdsctl previous TGID 300
```

Indexes are scanner protocol values reported by GSI or GLT. Verify control
behavior on the intended scanner before depending on it operationally.

## Themes

Built-in web, Home Assistant, and TUI themes are packaged independently.
Managed third-party packages can be validated before installation:

```bash
sdsctl themes validate /absolute/path/to/themes/web/my-theme
sdsctl themes install /absolute/path/to/themes/web/my-theme
sdsctl themes list
```

Home Assistant theme packages contain browser JavaScript and require an
additional explicit trust option. Read the
[theme package guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/themes.md)
before installing third-party code.

## Shell completion

Activate completion for the current shell:

```bash
eval "$(sdsctl completion bash)"
```

For Zsh:

```zsh
eval "$(sdsctl completion zsh)"
```

## Related tasks

- [Audio and recordings](Audio-and-Recordings)
- [Favorites and RadioReference](Favorites-and-RadioReference)
- [Operations and diagnostics](Operations-and-Diagnostics)
- [Python API](Python-API)
