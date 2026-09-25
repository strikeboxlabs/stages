# System stages

Run stages in order as root or with sudo. Keep all scripts and helpers together.

Stage 3 installs the CLIs, configures Hermes for Abliteration Large v2, and
enables and starts `hermes-backend.service` on `127.0.0.1:9119`. It prompts for
the API key once and reuses it on reruns, preserves unrelated configuration,
and checks backend health before reporting completion. The API key and config
backups have owner-only permissions. Provider API access is not tested.

Stage 4 reuses that key for Pi and configures SSH access for Hermes Desktop.
Use the same account for both stages (default: sudo-invoking user, or root when
run directly as root):

```sh
sudo ./stage3.sh --user root
sudo ./stage4.sh --user root
```

If stages 1–3 were already run with the old scripts, rerun stage 3, then stage 4;
there is no need to rerun stages 1–2 or run `hermes setup` separately.
The Hermes launcher is published at `/usr/local/bin/hermes`. Both managed
Hermes runtimes and older virtualenv layouts are supported. If overriding
`HERMES_HOME` or `HERMES_INSTALL_DIR`, use the same values for both stages.

Check the backend with `systemctl status hermes-backend.service`; inspect
startup failures with `journalctl -u hermes-backend.service -n 80`.
Use `--dry-run` on either script to preview actions.
