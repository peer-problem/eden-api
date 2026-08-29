"""Fail-closed parsers for production DB firewall and grant evidence."""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from collections.abc import Iterable

_UFW_PORT = "3306/tcp"
_NFT_PORT = re.compile(r"\btcp\s+dport\s+3306\b", re.IGNORECASE)
_GRANT = re.compile(
    r"^GRANT\s+(?P<privileges>.+?)\s+ON\s+"
    r"(?P<database>`[^`]+`|\*)\.(?P<object>`[^`]+`|\*)\s+TO\s+",
    re.IGNORECASE,
)
_REQUIRED_INGESTION = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
_ALLOWED_RUNTIME = frozenset({"USAGE", "SELECT"})


def _networks(raw: str) -> frozenset[str]:
    values = raw.replace(",", " ").split()
    if not values:
        raise ValueError("DB_ALLOWED_CIDRS must contain at least one network")
    networks = frozenset(
        str(ipaddress.ip_network(value, strict=False)) for value in values
    )
    if any(ipaddress.ip_network(value).prefixlen == 0 for value in networks):
        raise ValueError("DB_ALLOWED_CIDRS must not allow the entire internet")
    return networks


def _network(value: str) -> str:
    return str(ipaddress.ip_network(value.rstrip(","), strict=False))


def verify_ufw_rules(rules: str, allowed: str) -> None:
    expected = _networks(allowed)
    observed: set[str] = set()
    for raw_line in rules.splitlines():
        fields = raw_line.split()
        if not fields or fields[0].casefold() != _UFW_PORT:
            continue
        offset = 1
        if len(fields) > 1 and fields[1].casefold() == "(v6)":
            offset += 1
        if len(fields) <= offset + 2:
            raise ValueError("3306 ufw rule is incomplete")
        action, direction, source = fields[offset : offset + 3]
        if action.upper() != "ALLOW" or direction.upper() != "IN":
            raise ValueError("every 3306 ufw rule must be a narrow inbound allow")
        try:
            observed.add(_network(source))
        except ValueError as exc:
            raise ValueError(
                "every 3306 ufw rule must be a narrow inbound allow"
            ) from exc
    if not observed:
        raise ValueError("ufw has no 3306/tcp rule")
    if observed != expected:
        raise ValueError("ufw 3306 sources do not exactly match DB_ALLOWED_CIDRS")


def verify_nft_rules(rules: str, allowed: str) -> None:
    expected = _networks(allowed)
    observed: set[str] = set()
    for raw_line in rules.splitlines():
        if not _NFT_PORT.search(raw_line):
            continue
        fields = raw_line.replace(";", " ").split()
        lower = [field.casefold() for field in fields]
        if "accept" not in lower:
            raise ValueError("every 3306 nft rule must be an accept rule")
        sources: list[str] = []
        for index in range(len(fields) - 2):
            if lower[index] in {"ip", "ip6"} and lower[index + 1] == "saddr":
                sources.append(_network(fields[index + 2]))
        if len(sources) != 1:
            raise ValueError("every 3306 nft rule must contain one explicit source")
        observed.add(sources[0])
    if not observed:
        raise ValueError("nftables has no 3306 rule")
    if observed != expected:
        raise ValueError("nft 3306 sources do not exactly match DB_ALLOWED_CIDRS")


def _privileges(raw: str) -> frozenset[str]:
    return frozenset(value.strip().upper() for value in raw.split(",") if value.strip())


def verify_grants(grants: str, *, role: str, database: str) -> None:
    expected_database = database.casefold()
    accumulated: set[str] = set()
    matched = False
    for raw_line in grants.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _GRANT.match(line)
        if match is None:
            raise ValueError("unrecognized grant evidence")
        matched = True
        privileges = _privileges(match.group("privileges"))
        grant_database = match.group("database").strip("`").casefold()
        grant_object = match.group("object").strip("`")
        if "ALL PRIVILEGES" in privileges or "GRANT OPTION" in line.upper():
            raise ValueError("unbounded or delegable grants are forbidden")
        if privileges == {"USAGE"} and grant_database == "*" and grant_object == "*":
            continue
        if grant_database != expected_database or grant_object != "*":
            raise ValueError("grants must target only the configured database")
        accumulated.update(privileges)
    if not matched:
        raise ValueError("grant evidence is empty")
    if role == "runtime":
        if not accumulated or not accumulated <= _ALLOWED_RUNTIME or "SELECT" not in accumulated:
            raise ValueError("runtime user must have only SELECT on the configured database")
        return
    if not accumulated <= _REQUIRED_INGESTION:
        raise ValueError("ingestion user has unsupported privileges")
    missing = _REQUIRED_INGESTION - accumulated
    if missing:
        raise ValueError(
            "ingestion user is missing required DML grants: " + ", ".join(sorted(missing))
        )


def _read_stdin() -> str:
    return sys.stdin.read()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    firewall = subparsers.add_parser("firewall")
    firewall.add_argument("--format", choices=("ufw", "nft"), required=True)
    firewall.add_argument("--allowed", required=True)
    grants = subparsers.add_parser("grants")
    grants.add_argument("--role", choices=("runtime", "ingestion"), required=True)
    grants.add_argument("--database", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "firewall":
            if args.format == "ufw":
                verify_ufw_rules(_read_stdin(), args.allowed)
            else:
                verify_nft_rules(_read_stdin(), args.allowed)
        else:
            verify_grants(_read_stdin(), role=args.role, database=args.database)
    except ValueError as exc:
        print(f"DB security gate failed: {exc}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
