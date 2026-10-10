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

Stages 3 and 5 clone `https://github.com/strikeboxlabs/research-library.git` into
`/opt/research-library` when absent, then link its `skills` directory into the
selected account's `~/.hermes/skills/research-library` and
`~/.pi/agent/skills/research-library`. A custom `HERMES_HOME` is respected; use the
same account and override in both stages. Existing checkouts are validated and
reused without pulling or discarding local changes. Conflicting link destinations
are refused. Skills, references, and scripts remain in the checkout, so repository
updates are immediately reflected through both links. No tools described by the
skills are installed.

Stage 5 also backs up and patches Board's local Pi argument builder to retain
`--no-skills` and add `--skill /opt/research-library/skills` for execute, conclude,
and reason launches. Tool restrictions, model configuration, credentials, and
session resumption are preserved. The project-session helper stores transcripts
under `<project>/sessions/pi`; both helpers accept only known method hashes and
refuse unexpected upstream changes. Keep `configure-research-skills.py`,
`configure-board-skills.py`, and `configure-board-sessions.py` with the stage scripts.

Rerun stage 5 to integrate skills with an existing Board install. It restarts the
managed services, so let active work finish first. For links alone, without
reinstalling agents or changing their configuration, run:

```sh
sudo python3 configure-research-skills.py --user root --apply
```
