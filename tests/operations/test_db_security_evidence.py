from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.verify_db_security import (
    verify_grants,
    verify_nft_rules,
    verify_ufw_rules,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_ufw_requires_the_exact_approved_source_set() -> None:
    verify_ufw_rules(
        "3306/tcp ALLOW IN 203.0.113.7\n3306/tcp (v6) ALLOW IN 2001:db8::/64\n",
        "203.0.113.7/32,2001:db8::/64",
    )

    with pytest.raises(ValueError, match="exactly match"):
        verify_ufw_rules(
            "3306/tcp ALLOW IN 203.0.113.7\n3306/tcp ALLOW IN 198.51.100.4\n",
            "203.0.113.7/32",
        )
    with pytest.raises(ValueError, match="narrow inbound allow"):
        verify_ufw_rules("3306/tcp ALLOW IN Anywhere\n", "203.0.113.7/32")


def test_nft_requires_port_source_and_action_on_the_same_rule() -> None:
    verify_nft_rules(
        "ip saddr 203.0.113.0/24 tcp dport 3306 counter accept\n",
        "203.0.113.0/24",
    )

    with pytest.raises(ValueError, match="explicit source"):
        verify_nft_rules(
            "tcp dport 3306 accept\nip saddr 203.0.113.0/24 accept\n",
            "203.0.113.0/24",
        )
    with pytest.raises(ValueError, match="accept rule"):
        verify_nft_rules(
            "ip saddr 203.0.113.0/24 tcp dport 3306 drop\n",
            "203.0.113.0/24",
        )


def test_database_grants_reject_select_only_and_wrong_schema() -> None:
    runtime = "GRANT USAGE ON *.* TO `reader`@`%`\nGRANT SELECT ON `eden`.* TO `reader`@`%`"
    verify_grants(runtime, role="runtime", database="eden")

    with pytest.raises(ValueError, match="missing required DML"):
        verify_grants(
            "GRANT SELECT ON `eden`.* TO `writer`@`%`",
            role="ingestion",
            database="eden",
        )
    with pytest.raises(ValueError, match="configured database"):
        verify_grants(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON `other`.* TO `writer`@`%`",
            role="ingestion",
            database="eden",
        )
    verify_grants(
        "GRANT USAGE ON *.* TO `writer`@`%`\n"
        "GRANT SELECT, INSERT, UPDATE, DELETE ON `eden`.* TO `writer`@`%`",
        role="ingestion",
        database="eden",
    )


def _fake_command(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_ssh_pin_rejects_a_mixed_advertised_key_set(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_command(fake_bin / "ssh-keyscan", "printf \"host key-material\\n\"\n")
    _fake_command(
        fake_bin / "ssh-keygen",
        "printf \"256 SHA256:expected host (ED25519)\\n\"\n"
        "printf \"256 SHA256:rogue host (RSA)\\n\"\n",
    )
    raw = tmp_path / "raw"
    pinned = tmp_path / "pinned"
    command = (
        "EDEN_OPS_LIBRARY_ONLY=true source .ops/deploy.sh; "
        f"verify_ssh_host_key host SHA256:expected {raw} {pinned}"
    )

    result = subprocess.run(  # noqa: S603 - isolated fake PATH and fixed test command
        ["/bin/bash", "-c", command],
        cwd=REPOSITORY_ROOT,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "advertised keys do not exactly match the pin" in result.stderr
