#!/usr/bin/env bash
# Stage 1 SSH access, independent of agent installation and disk expansion.
set -Eeuo pipefail

ssh_die() { printf 'Error [stage1 SSH]: %s\n' "$*" >&2; exit 1; }

configure_root_password_login() {
    local directory=$1 dropin previous effective valid=true
    dropin=$directory/00-stage1-root-login.conf
    install -d -m 0755 "$directory"
    if [[ -L "$dropin" ]] || { [[ -e "$dropin" ]] && ! grep -qx '# Managed by stage1.sh' "$dropin"; }; then
        ssh_die "Refusing to overwrite unmanaged file: $dropin"
    fi
    previous=$(mktemp)
    if [[ -e "$dropin" ]]; then cp -p "$dropin" "$previous"; fi
    cat > "$dropin" <<'EOF'
# Managed by stage1.sh
# Password access from the workstation before agent/API-key setup.
Match User root
    PermitRootLogin yes
    PasswordAuthentication yes
Match all
EOF
    chmod 0644 "$dropin"
    # Validate the real effective configuration: an earlier Match or a missing
    # Include must not make us report password access that is still disabled.
    if ! sshd -t || ! effective=$(sshd -T -C user=root,host=localhost,addr=127.0.0.1); then
        valid=false
    else
        effective=${effective,,}
        grep -qx 'permitrootlogin yes' <<< "$effective" || valid=false
        grep -qx 'passwordauthentication yes' <<< "$effective" || valid=false
        # A policy requiring an additional public key would defeat console bootstrap.
        awk '$1 == "authenticationmethods" { for (i=2; i<=NF; i++) if ($i == "any" || $i == "password") ok=1 } END { exit !ok }' \
            <<< "$effective" || valid=false
    fi
    if ! "$valid"; then
        if [[ -s "$previous" ]]; then cp -p "$previous" "$dropin"; else rm -f -- "$dropin"; fi
        rm -f -- "$previous"
        ssh_die 'SSH policy prevents root password login. Ensure sshd_config includes /etc/ssh/sshd_config.d/*.conf and review earlier Match/authentication rules; the previous drop-in was restored.'
    fi
    rm -f -- "$previous"
}

ensure_root_password() {
    local account state rest status
    status=$(passwd -S root) || ssh_die 'Could not inspect the root password status.'
    read -r account state rest <<< "$status"
    if [[ "$state" != P ]]; then
        [[ -t 0 ]] || ssh_die 'Root needs a usable password. Run passwd root in the VM console, then rerun stage1.sh.'
        printf '\nSet the root password for the first SSH login from your workstation.\n'
        passwd root || ssh_die 'Could not set the root password.'
        status=$(passwd -S root) || ssh_die 'Could not inspect the root password status.'
        read -r account state rest <<< "$status"
        [[ "$state" == P ]] || ssh_die 'Root still has no usable password.'
    fi
}

bootstrap_ssh() {
    local script_dir tool ssh_effective ssh_ports firewall_helper firewall_unit managed port
    local missing=false
    if (( $# == 1 )) && [[ "$1" == --dry-run ]]; then
        printf '%s\n' \
            '[stage1] Would install and start OpenSSH before disk expansion or agent setup.' \
            'Would enable root password login; keep an existing password or prompt if root is locked/passwordless.' \
            'Would generate missing host keys, validate SSH, and persistently allow only SSH ports through the local IPv4 firewall.' \
            'Would enable SSH at boot and print the workstation connection command.'
        return
    fi
    (( $# == 0 )) || ssh_die 'Unknown SSH bootstrap option.'
    (( EUID == 0 )) || ssh_die 'Run stage1.sh as root or with sudo.'
    [[ -d /run/systemd/system ]] || ssh_die 'A running systemd system is required to enable SSH.'
    export PATH="$PATH:/usr/sbin:/sbin"
    script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
    [[ -f "$script_dir/configure-ssh-firewall.py" ]] || ssh_die 'Missing configure-ssh-firewall.py; keep stage helpers together.'
    for tool in sshd ssh-keygen nft iptables-legacy python3 passwd; do
        command -v "$tool" >/dev/null || missing=true
    done
    if "$missing"; then
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server nftables iptables python3 passwd
    fi
    ssh-keygen -A
    install -d -m 0755 /run/sshd
    configure_root_password_login /etc/ssh/sshd_config.d
    ensure_root_password
    ssh_effective=$(sshd -T -C user=root,host=localhost,addr=127.0.0.1)
    ssh_ports=$(awk 'tolower($1) == "port" { print $2 }' <<< "$ssh_effective" | sort -nu | tr '\n' ' ')
    [[ -n "$ssh_ports" ]] || ssh_die 'Could not determine SSH listening ports.'

    firewall_helper=/usr/local/libexec/stages-ssh-firewall
    firewall_unit=/etc/systemd/system/stages-ssh-firewall.service
    for managed in "$firewall_helper" "$firewall_unit"; do
        if [[ -L "$managed" ]] || { [[ -e "$managed" ]] && ! grep -Eq '^# Managed by (stage1\.sh|stages SSH setup\.)$' "$managed"; }; then
            ssh_die "Refusing to overwrite unmanaged file: $managed"
        fi
    done
    install -d -m 0755 /usr/local/libexec
    install -m 0755 "$script_dir/configure-ssh-firewall.py" "$firewall_helper"
    cat > "$firewall_unit" <<EOF
# Managed by stage1.sh
[Unit]
Description=Allow early SSH access through the local firewall
After=nftables.service ufw.service firewalld.service netfilter-persistent.service
Before=ssh.service

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/stages-ssh-firewall $ssh_ports
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "$firewall_unit"
    systemd-analyze verify "$firewall_unit"
    systemctl daemon-reload
    systemctl enable stages-ssh-firewall.service
    systemctl restart stages-ssh-firewall.service
    systemctl enable --now ssh
    # Reload applies the new policy on reruns without dropping existing sessions.
    systemctl reload ssh
    systemctl is-active --quiet ssh || ssh_die 'SSH did not start. Inspect: systemctl status ssh'
    printf '\n[stage1] Root password SSH access is enabled now and at boot.\n'
    printf 'VM network addresses: '
    hostname -I
    for port in $ssh_ports; do printf 'From your workstation: ssh -p %s root@<VM-IP>\n' "$port"; done
    printf 'Use the root password. Continue stages 2-4 from that SSH session after stage 1 finishes.\n'
    printf 'The VMware network and external firewalls must allow the SSH port.\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    bootstrap_ssh "$@"
fi
