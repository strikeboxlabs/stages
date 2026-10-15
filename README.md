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

## Optional offensive/research skills

Stages 3 and 5 no longer clone research-library or add its Board skill arguments.
Provision it separately on the computer intended to host that library:

```sh
sudo ./install-offensive-tools.sh --user root --dry-run
sudo ./install-offensive-tools.sh --user root
```

Use the same account as the Board dispatcher. Run after stage 5 to integrate
Board; without Board, the installer sets up Hermes/Pi links and can be rerun
later. It clones `https://github.com/strikeboxlabs/research-library.git` into
`/opt/research-library` and links its skills into the selected account's
`~/.hermes/skills/research-library` and `~/.pi/agent/skills/research-library`.
A custom `HERMES_HOME` is respected. Existing checkouts are validated and reused
without pulling or discarding local edits; conflicting destinations are refused.

When Board is present, the installer backs up and patches its local Pi argument
builder to retain `--no-skills` and add `--skill /opt/research-library/skills`.
Tool restrictions, model configuration and session handling are preserved.
An active dispatcher is restarted; finish active work first. The installer
provisions the library's skills/references/scripts and installs tools available
as Kali packages. `kali-research-packages.py` maps skill entrypoints and shell
command examples to the current Kali package/Contents indexes, including known
command/package aliases. It saves a deduplicated plan and deferred skill list
in `/var/lib/stages/offensive-packages.json`, installs with APT, and checks package
status. Tools without a resolved Kali package are deferred; no pip, gems, source
builds, or third-party repositories are used. APT handles package dependencies
and normal package configuration. This can install large suites and service
packages; it does not execute the library's scanner examples.

Dry runs read existing indexes without updating them; normal runs install
`apt-file` and refresh the indexes first. Keep `configure-research-skills.py`,
`configure-board-skills.py`, and `kali-research-packages.py` alongside the installer.

Stage 5 independently stores Pi transcripts under `<project>/sessions/pi` using
`configure-board-sessions.py`. All adapter helpers refuse unknown upstream method
versions. Existing research-library checkouts, links and Board patches from older
stage runs are preserved; moving provisioning does not uninstall them.

Keep stage scripts, their helpers, and reusable tests in this repository. Store
temporary development workspaces, screenshots, audit output, and rollback backups
outside the checkout. `.gitignore` excludes local work folders, credential
directories, dependencies, caches, archives, and database snapshots. Review
`git status --short` and the staged diff before committing.

## SAST tool installation

`install-sast-tools.sh` provisions the seven CLI tools documented by
`/opt/sast`: Semgrep Community Edition, CodeQL (including compatible queries),
Bandit, gosec, Brakeman, Cppcheck, and Trivy. It is independent of stages 1–5.

```sh
./install-sast-tools.sh --dry-run
sudo ./install-sast-tools.sh --user root
# A different catalogue checkout:
sudo ./install-sast-tools.sh --repo /path/to/sast
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
It creates `/opt/sast-tools/skills` pointing to the selected checkout and adds
`--skill /opt/sast-tools/skills` to existing Board local Pi launches while keeping
`--no-skills` and transcript settings. Use the Board dispatcher account. An active
dispatcher is restarted; finish active work first. Without Board, rerun after
stage 5 to integrate it. The checkout must remain readable by the worker account.
Conflicting skill destinations are refused; unrelated content is preserved.

It does not run source scans, download Trivy databases, or install every
possible application's build dependencies. Rules, databases, and project-specific
SDKs may need additional setup when running the skills.
