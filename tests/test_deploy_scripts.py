"""The production deployment scripts: syntax, provider branching, firewall rules.

Offline. The bootstrap library exposes test seams (environment overrides and a
dry-run mode) so none of this needs a VM, root, or a Docker daemon -- except
the nginx check, which runs `nginx -t` in a container and is marked `docker`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "scripts" / "deploy"
LIB = DEPLOY / "lib" / "common.sh"

SCRIPTS = [
    *sorted(DEPLOY.glob("*.sh")),
    LIB,
    ROOT / "scripts" / "check_deployment.sh",
    ROOT / "nginx" / "40-select-conf.sh",
]


def bash(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Run *script* in bash with the library sourced and *env* added."""
    return subprocess.run(
        ["bash", "-c", f". {LIB}; {script}"],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=False,
    )


@pytest.fixture
def resolv(tmp_path: Path):
    def write(*nameservers: str) -> str:
        path = tmp_path / "resolv.conf"
        path.write_text("".join(f"nameserver {ns}\n" for ns in nameservers) + "search x\n")
        return str(path)

    return write


class TestSyntax:
    @pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: str(p.relative_to(ROOT)))
    def test_every_script_parses(self, script: Path) -> None:
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    @pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
    def test_shellcheck_finds_no_errors(self) -> None:
        result = subprocess.run(
            ["shellcheck", "--severity=error", *map(str, SCRIPTS)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout


class TestFirewallRules:
    def rules(self, provider: str, resolv_conf: str, **env: str) -> list[str]:
        result = bash(
            f"container_firewall_rules {provider}",
            REPOLAB_RESOLV_CONF=resolv_conf,
            REPOLAB_EXTERNAL_IFACE=env.pop("iface", "ens4"),
            **env,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.splitlines()

    def test_gcp_keeps_dns_to_the_metadata_server_and_drops_the_rest(self, resolv) -> None:
        """Google's metadata server is also the VM's DNS resolver."""
        rules = self.rules("gcp", resolv("169.254.169.254"))
        dns_udp = "iptables -d 169.254.169.254/32 -p udp --dport 53 -j RETURN"
        dns_tcp = "iptables -d 169.254.169.254/32 -p tcp --dport 53 -j RETURN"
        drop = "iptables -d 169.254.0.0/16 -j DROP"
        assert dns_udp in rules
        assert dns_tcp in rules
        assert rules.index(dns_udp) < rules.index(drop)
        assert rules.index(dns_tcp) < rules.index(drop)
        # Listed once, though it is both the resolver and the metadata server.
        assert rules.count(dns_udp) == 1

    def test_gcp_drops_the_ipv6_metadata_address(self, resolv) -> None:
        assert "ip6tables -d fd20:ce::254/128 -j DROP" in self.rules("gcp", resolv())

    def test_nebius_drops_link_local_and_the_resolved_metadata_address(self, resolv) -> None:
        rules = self.rules("nebius", resolv(), REPOLAB_NEBIUS_METADATA_IP="169.254.10.10")
        assert "iptables -d 169.254.0.0/16 -j DROP" in rules
        assert "iptables -d 169.254.10.10/32 -j DROP" in rules
        assert not any("--dport 53" in rule and "169.254" in rule for rule in rules)

    @pytest.mark.parametrize("provider", ["gcp", "nebius"])
    def test_a_private_resolver_stays_reachable_for_dns_only(self, resolv, provider) -> None:
        rules = self.rules(provider, resolv("10.0.0.2"), REPOLAB_NEBIUS_METADATA_IP="")
        allow = "iptables -d 10.0.0.2/32 -p udp --dport 53 -j RETURN"
        private = "iptables -o ens4 -d 10.0.0.0/8 -j DROP"
        assert rules.index(allow) < rules.index(private)
        assert not any(rule.startswith("iptables -d 10.0.0.2/32 -j") for rule in rules)

    @pytest.mark.parametrize("provider", ["gcp", "nebius"])
    def test_cloud_internal_ranges_are_dropped_only_out_of_the_external_nic(
        self, resolv, provider
    ) -> None:
        rules = self.rules(provider, resolv(), REPOLAB_NEBIUS_METADATA_IP="")
        private = [r for r in rules if any(n in r for n in ("10.0.0.0/8", "172.16.0.0/12"))]
        assert private
        # Matched on the outgoing interface: Docker's own bridges are untouched.
        assert all("-o ens4" in rule for rule in private)

    def test_the_stub_resolver_is_never_listed(self, resolv) -> None:
        rules = self.rules("gcp", resolv("127.0.0.53"))
        assert not any("127.0.0.53" in rule for rule in rules)

    def test_returns_precede_every_drop(self, resolv) -> None:
        rules = self.rules("gcp", resolv("169.254.169.254", "10.0.0.2"))
        last_return = max(i for i, r in enumerate(rules) if r.endswith("RETURN"))
        first_drop = min(i for i, r in enumerate(rules) if r.endswith("DROP"))
        assert last_return < first_drop

    def test_no_interface_means_no_private_rules_and_a_warning(self, resolv) -> None:
        result = bash(
            "container_firewall_rules gcp",
            REPOLAB_RESOLV_CONF=resolv(),
            REPOLAB_EXTERNAL_IFACE="",
        )
        # With no default route found, nothing is blocked blindly.
        assert result.returncode == 0
        assert "-o " not in result.stdout
        assert "private ranges not blocked" in result.stderr

    def test_an_unknown_provider_is_refused(self, resolv) -> None:
        result = bash(
            "container_firewall_rules aws",
            REPOLAB_RESOLV_CONF=resolv(),
            REPOLAB_EXTERNAL_IFACE="eth0",
        )
        assert result.returncode != 0
        assert "unknown provider" in result.stderr


class TestDockerDaemonJson:
    def merge(self, path: Path, mtu: str) -> subprocess.CompletedProcess[str]:
        return bash(
            "merge_docker_daemon_json", DOCKER_DAEMON_JSON=str(path), REPOLAB_EXTERNAL_MTU=mtu
        )

    def test_adds_log_rotation_and_the_network_mtu(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.json"
        assert self.merge(path, "1460").stdout.strip() == "changed"
        config = json.loads(path.read_text())
        assert config["log-opts"] == {"max-size": "20m", "max-file": "5"}
        assert config["mtu"] == 1460
        assert config["default-network-opts"]["bridge"]["com.docker.network.driver.mtu"] == "1460"

    def test_a_standard_mtu_is_left_to_docker(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.json"
        self.merge(path, "1500")
        assert "mtu" not in json.loads(path.read_text())

    def test_existing_settings_are_preserved(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.json"
        path.write_text(json.dumps({"data-root": "/srv/docker", "log-opts": {"max-size": "5m"}}))
        result = self.merge(path, "1460")
        config = json.loads(path.read_text())
        assert config["data-root"] == "/srv/docker"
        # The operator's value wins, and the difference is reported.
        assert config["log-opts"] == {"max-size": "5m"}
        assert "already sets log-opts" in result.stderr

    def test_a_second_run_changes_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "daemon.json"
        self.merge(path, "1460")
        before = path.read_text()
        assert self.merge(path, "1460").stdout.strip() == "unchanged"
        assert path.read_text() == before


class TestBootstrapBranching:
    def dry_run(self, script: str) -> list[str]:
        result = subprocess.run(
            ["bash", str(DEPLOY / script), "tester"],
            capture_output=True,
            text=True,
            env={**os.environ, "REPOLAB_BOOTSTRAP_DRY_RUN": "1"},
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return [line for line in result.stdout.splitlines() if line.startswith("step: ")]

    COMMON: ClassVar[list[str]] = [
        "step: check_os",
        "step: install_docker",
        "step: configure_docker_daemon",
        "step: add_docker_user tester",
        "step: create_state_dirs",
    ]

    def test_gcp_runs_the_common_steps_then_its_own(self) -> None:
        steps = self.dry_run("bootstrap_gcp.sh")
        assert steps[: len(self.COMMON)] == self.COMMON
        assert "step: check_gcp_identity" in steps
        assert any(s.startswith("step: install_container_firewall gcp ") for s in steps)
        assert steps[-1] == "step: write_provider_marker gcp"

    def test_nebius_runs_the_common_steps_then_its_own(self) -> None:
        steps = self.dry_run("bootstrap_nebius.sh")
        assert steps[: len(self.COMMON)] == self.COMMON
        assert "step: check_nebius_identity" in steps
        assert any(s.startswith("step: install_container_firewall nebius ") for s in steps)
        assert steps[-1] == "step: write_provider_marker nebius"

    def test_the_old_entry_point_is_the_nebius_bootstrap(self) -> None:
        assert self.dry_run("bootstrap_vm.sh") == self.dry_run("bootstrap_nebius.sh")

    def test_a_deploy_user_is_required(self) -> None:
        result = subprocess.run(
            ["bash", str(DEPLOY / "bootstrap_gcp.sh")],
            capture_output=True,
            text=True,
            env={**os.environ, "REPOLAB_BOOTSTRAP_DRY_RUN": "1", "SUDO_USER": ""},
            check=False,
        )
        assert result.returncode != 0
        assert "deploy-user" in result.stderr


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
class TestCompose:
    def test_the_production_stack_is_valid_for_any_provider(self, tmp_path: Path) -> None:
        env = tmp_path / "test.env"
        env.write_text(
            "POSTGRES_PASSWORD=x\nREDIS_PASSWORD=y\n"
            "PUBLIC_URL=http://203.0.113.10\nREPOLAB_DOMAIN=203.0.113.10\n"
        )
        result = subprocess.run(
            ["docker", "compose", "-f", "docker-compose.prod.yml", "--env-file", str(env),
             "config", "--quiet"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )  # fmt: skip
        assert result.returncode == 0, result.stderr


@pytest.mark.docker
class TestNginx:
    """Render each template the way the nginx image does, then `nginx -t` it."""

    SELECTOR = "/docker-entrypoint.d/15-repolab-select-conf.sh"

    @pytest.mark.parametrize("mode", ["http", "https"])
    def test_the_config_is_accepted(self, tmp_path: Path, mode: str) -> None:
        domain = "repolab.example"
        live = tmp_path / "letsencrypt" / "live" / domain
        live.mkdir(parents=True)
        if mode == "https":
            if shutil.which("openssl") is None:
                pytest.skip("openssl not installed")
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-subj", f"/CN={domain}", "-keyout", str(live / "privkey.pem"),
                 "-out", str(live / "fullchain.pem")],
                capture_output=True,
                check=True,
            )  # fmt: skip
        result = subprocess.run(
            ["docker", "run", "--rm",
             "-e", f"REPOLAB_DOMAIN={domain}", "-e", "NGINX_ENVSUBST_FILTER=REPOLAB_",
             "-v", f"{ROOT / 'nginx' / 'templates'}:/etc/repolab/nginx:ro",
             "-v", f"{ROOT / 'nginx' / '40-select-conf.sh'}:{self.SELECTOR}:ro",
             "-v", f"{tmp_path / 'letsencrypt'}:/etc/letsencrypt:ro",
             "nginx:1.27-alpine", "nginx", "-t"],
            capture_output=True,
            text=True,
            check=False,
        )  # fmt: skip
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert f"repolab: serving {mode} for {domain}" in output
        assert "test is successful" in output
