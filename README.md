# System stages

Run stages in order as root or with sudo. Keep all scripts and helpers together.

Stage 3 installs the CLIs and prompts independently for Hermes and Pi. For each
agent, choose a model and enter its API key with hidden input:

1. Abliteration.ai Large v2 (`abliterated-model-large-v2`).
2. Z.ai GLM-5.3 (`glm-5.3`).
3. DeepSeek Flash (Max) (`deepseek-flash`).

Z.ai uses the GLM Coding Plan subscription endpoint,
`https://api.z.ai/api/coding/paas/v4`. Enter your Coding Plan API key when choosing
GLM-5.3. See the
[Z.ai connection guide](https://zcode.z.ai/en/docs/configuration).

DeepSeek uses `https://api.deepseek.com` and a DeepSeek API key.
DeepSeek Flash is configured with Max reasoning for both agents, including on reruns.
See the [DeepSeek model details](https://api-docs.deepseek.com/quick_start/pricing/) and
[thinking mode guide](https://api-docs.deepseek.com/guides/thinking_mode/).

Hermes and Pi can use different providers or different keys for the same
provider. Hermes settings are saved in `HERMES_HOME/config.yaml`; Pi settings
and credentials are saved in `~/.pi/agent`.

Reruns prompt for both agents again, each with its own selection as the default.
At each key prompt, press Enter to keep that agent's saved key when available,
or enter a replacement. A key is kept only for the same endpoint and agent.
Both selections are collected before writing either agent's configuration.
Unrelated configuration is preserved, with owner-only backups. All
models use a 1M-token context window; GLM-5.3 and DeepSeek are configured with reasoning enabled
and a supported effort level, as described in the GLM
[model documentation](https://docs.z.ai/guides/llm/glm-5.3).

Stage 3 enables and starts `hermes-backend.service` on `127.0.0.1:9119` and checks
backend health before reporting completion. The API key and config backups have
owner-only permissions. Provider API access is not tested. Keep
`model_providers.py` with the configuration helpers and stage scripts.

Stage 4 verifies Pi's saved model offline and configures SSH access for Hermes
Desktop. It preserves the independent model and key choices from stage 3.
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

## Local daemon setup

Use the same existing Linux account for stages 3–5 and the optional tool-and-skill installer.
The API has its own `board` service account; the dispatcher and Pi workers use the
selected account and its normal `~/.pi/agent` model/auth configuration.

1. Run stages 1 and 2, then stage 3 and stage 4 with `--user YOUR_ACCOUNT`.
   Stage 3 pins Pi **1.3.0** and requires Node.js **22.19.0 or newer**. The host's
   APT repositories supply Node; if they supply an older version, update that
   prerequisite before continuing. npm engine checks remain enabled.
2. Run `sudo ./stage5.sh --user YOUR_ACCOUNT`. A fresh native Board setup stores
   sessions in `<project>/sessions/pi`, creates `dispatch.yaml`, and starts the
   local API and dispatcher. Skills start disabled until a library is registered.
3. Before adding/upgrading tools or libraries, finish active work and stop
   `board-dispatcher.service`. Run the optional `install-tools.sh` described below. It
   refuses to modify a host while its dispatcher is active and do not restart it.
4. Check profiles and readiness under the actual dispatcher account, then start
   the dispatcher when ready:

```sh
sudo -H -u YOUR_ACCOUNT -g board /opt/board/board/.venv/bin/board skills doctor --config /opt/board/dispatch.yaml --json
sudo -H -u YOUR_ACCOUNT -g board /opt/board/board/.venv/bin/board skills profiles --config /opt/board/dispatch.yaml --json
sudo systemctl start board-dispatcher.service
```

These checks are local and make no model requests. They verify configuration and
resource access; they do not establish vulnerability-finding performance. Test
first on a disposable host before relying on a production daemon upgrade.

Stage 3 accepts an explicit future Pi upgrade through `--pi-version VERSION` or
`PI_VERSION`; version ranges and `latest` are refused. Provider authentication
behavior is unchanged. Before rerunning stages 3/5 or the optional tool installer, stop
the dispatcher deliberately after active work has finished.

## Upgrading older staged hosts

Stages no longer rewrite Board Python source. Existing adapter patches, backups,
credentials, evidence, and session directories are preserved. An old Board build
without native `skills doctor` support is refused with an upgrade message.
Update `/opt/board` to a reviewed native-profile release first, resolving old
managed source patches in that application checkout deliberately; stage 5 does
not pull, reset, or discard them. Then rerun stage 5 and `install-tools.sh` with the groups required by the host. Existing configs are migrated idempotently; explicit
session storage choices and custom skill profiles/budgets are retained.

The config helpers use Board's virtualenv Python/PyYAML, validate a candidate
before publication, and create owner-only configuration backups under
`/var/lib/stages/board-config-backups`, outside the application checkout. They preserve
other top-level YAML sections and surrounding comments. No provider/auth file is
edited by skill/session migration. Existing `catalog.json` files must accompany
library checkouts; update older libraries deliberately rather than relying on an
automatic pull.

## Optional tool and skill groups

Stages 3 and 5 no longer clone research-library or add its Board skill arguments.
Use one optional installer, independently of stage 5. On Kali the default selects
both groups; on plain Debian/Ubuntu select only `sast`:

```sh
sudo ./install-tools.sh --user YOUR_ACCOUNT --dry-run
sudo ./install-tools.sh --user YOUR_ACCOUNT
# Select one group explicitly:
sudo ./install-tools.sh --groups research --user YOUR_ACCOUNT
sudo ./install-tools.sh --groups sast --user YOUR_ACCOUNT
```

Use the same account as the Board dispatcher. Run after stage 5 to integrate
Board; without Board, the installer sets up Hermes/Pi links and can be rerun
later. An active `board-dispatcher.service` is stopped during provisioning and
started again on exit, including failures, Ctrl-C and termination. A dispatcher
that was inactive stays inactive. Dry runs leave the service untouched. Concurrent
installations are refused. The `research` group requires Kali and clones `https://github.com/strikeboxlabs/research-library.git` into
`/opt/research-library` and links its skills into the selected account's
`~/.hermes/skills/research-library` and `~/.pi/agent/skills/research-library`.
A custom `HERMES_HOME` is respected. Existing checkouts are validated and reused
without pulling or discarding local edits; conflicting destinations are refused.

When Board is present, the installer registers
`/opt/research-library/catalog.json` in `dispatch.yaml`, enables curated profiles,
and runs native Board validation as the selected dispatcher account. It preserves
custom profile definitions, the selected default profiles, metadata budget, model
configuration, worker tools, and session handling. A new skill configuration uses
`general` and a 6000-token metadata budget; this is a routing budget, not a proven
model capacity limit. Board resolves selected package directories per worker and
passes them explicitly to Pi alongside `--no-skills`.

The `research` group also installs SecLists wordlists at `/usr/share/seclists`
and tools available as Kali packages.
`kali-research-packages.py` maps skill entrypoints and shell command examples to
the current Kali package/Contents indexes, including known command/package aliases.
It saves a deduplicated plan and deferred skill list in
`/var/lib/stages/offensive-packages.json`, installs with APT, and checks package
status. Tools without a resolved Kali package are deferred; no pip, gems, source
builds, or third-party repositories are used. APT handles package dependencies and
normal package configuration. This can install large suites and service packages;
it does not execute skill scanner examples. Package installation does not mean a
skill's task prerequisites have all been verified.

Dry runs read existing indexes without updating them; normal runs install
`apt-file` and refresh the indexes first. Keep all Python helpers, including
`board_config.py`, and the `lib/` directory alongside the installer. Core stages do not clone either skill
library or install their tools. They preserve any already registered catalogs.

Packages remain in their shared library checkouts. Hermes/Pi directory links are
for normal interactive discovery; Board's configured catalog/profile selection is
independent. Changing one worker's selected profile does not mutate global links
or expose every installed package to every worker. A Linux execution host can
carry knowledge about other target platforms; host readiness and target
classification are separate facts.

Keep stage scripts, their helpers, and reusable tests in this repository. Store
temporary development workspaces, screenshots, audit output, and rollback backups
outside the checkout. `.gitignore` excludes local work folders, credential
directories, dependencies, caches, archives, and database snapshots. Review
`git status --short` and the staged diff before committing.

## SAST tool installation

The `sast` group in `install-tools.sh` clones/reuses the public
`strikeboxlabs/sast` repository at `/opt/sast` and provisions its seven CLI tools: Semgrep Community Edition, CodeQL (including compatible queries),
Bandit, gosec, Brakeman, Cppcheck, and Trivy. It is independent of stages 1–5.

```sh
./install-tools.sh --groups sast --dry-run
sudo ./install-tools.sh --groups sast --user YOUR_ACCOUNT
# An explicitly selected existing tree (Git metadata is optional):
sudo ./install-tools.sh --groups sast --sast-repo /path/to/sast --user YOUR_ACCOUNT
```

Requires network access on Debian/Kali (x86-64 or ARM64). Commands are available
system-wide in `/usr/local/bin` (Cppcheck comes from apt). Python environments,
Ruby gems, and release bundles live under `/opt/sast-tools`; system Python is
not modified with pip. Reruns upgrade managed installations and refuse unrelated
launcher collisions. CodeQL bundles are large and require substantial free disk
space, including room to download and extract before publication. Old CodeQL
release directories are retained. CodeQL licensing still applies to its use.

The installer checks executable versions and CodeQL resources, links skills into
`~/.hermes/skills/sast` and `~/.pi/agent/skills/sast` for `--user` (defaults to
the sudo-invoking user, otherwise the current user), and honors `HERMES_HOME`.
It creates `/opt/sast-tools/skills` and `/opt/sast-tools/catalog.json` symlinks to
the selected checkout, then registers the stable catalog path in existing Board
configuration. Board receives selected package directories through native skill
profiles. Use the dispatcher account when provisioning. Without
Board, rerun after stage 5 to register the catalog. The checkout and supporting
resources must remain readable by that account. Conflicting destinations are
refused; unrelated content is preserved. Restoring the dispatcher can resume its
existing projects. SIGKILL or power loss prevents automatic service restoration.

It does not run source scans, download Trivy databases, or install every
possible application's build dependencies. Rules, databases, and project-specific
SDKs may need additional setup when running the skills.

The old `install-offensive-tools.sh` and `install-sast-tools.sh` filenames are
compatibility aliases only. New automation should call `install-tools.sh` with
explicit groups. Internal group implementations are not separate public installers.
Default managed checkout origins are validated; no existing checkout is pulled or
reset. An explicit `--sast-repo` remains an administrator-selected existing tree.
Malformed SAST catalogs are rejected before package installation.

For rollback, finish active work and stop the dispatcher. Revert the single
relevant repository commit and restore the appropriate pre-migration `dispatch.yaml`
from `/var/lib/stages/board-config-backups`, preserving its original owner and mode.
An older Board version rejects native configuration fields, so restore its matching
configuration too. Retain project data and sessions; rollback does not delete the
database or evidence. No automatic rollback is performed.

## Validation

Run fixture tests on Linux using a Python environment with PyYAML (Board's venv
already has it): `python3 -m unittest discover -s tests -v`. Tests do not call a
model or execute source-analysis scans. Installer planning fixtures use `--dry-run`;
symlink, ownership, and shell-wrapper behavior require Linux.

For the native-profile change, **16 tests passed** on the Windows authoring host:
12 configuration fixtures and four integrations using the actual native Board CLI
in subprocesses. Synthetic local catalogs covered disabled loading, registering
both libraries, session migration, profile summaries, invalid catalog rejection,
and missing packages. No doctor/native command was mocked in those integrations.
All eight shell scripts also passed `bash -n`. Fixture coverage includes YAML/config
preservation, idempotence, validation-before-publication, dispatcher identity
arguments, rejected legacy builds, and concurrent-edit protection.
The Debian/Kali staging process, systemd services, tool installs, real Pi discovery,
and resumed worker behavior still require a disposable Linux host validation pass.

CLI integration tests require native-profile Board installed in the test Python
environment. They invoke its console entrypoint (`from board.cli import main`);
Board currently has no `board.__main__` module for `python -m board`.

Unified-installer validation: three portable checkout/catalog fixtures passed on
the Windows authoring host; three shell planning fixtures require Linux and were
skipped there. All eleven root/internal shell files passed `bash -n`. The Linux
CI job runs all selected-group planning and compatibility-alias cases without
executing installation branches or scanners.
