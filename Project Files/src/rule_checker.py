"""
rule_checker.py -- deterministic configuration checks. No AI involved.

This module is the counterweight to the AI layer. It cannot reason about a novel fault,
but it also cannot hallucinate: every finding it produces is anchored to a line it
actually matched in the transcript. Where the checker and the AI disagree, that
disagreement is itself the most useful signal the pipeline produces.

The problem statement asks for checks covering duplicate IPs, wrong masks, gateway
mismatch, interface down, missing VLANs and missing routes. Those six are implemented
plus twenty-two more, organised by layer.

Adding a check is one function and one decorator:

    @check("my_check_id", "High")
    def check_my_thing(case):
        yield Finding(...)

Every Finding must carry `evidence` -- the verbatim transcript line that triggered it.
A check that cannot quote its trigger line is not auditable and does not belong here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterator

# --------------------------------------------------------------------------- model


@dataclass
class Finding:
    """One deterministic observation about a case."""

    check_id: str
    severity: str
    message: str
    evidence: str
    suggested_command: str = ""

    def __str__(self) -> str:
        out = f"[{self.severity:<8}] {self.check_id}: {self.message}"
        if self.evidence:
            out += f"\n             evidence | {self.evidence.strip()}"
        if self.suggested_command:
            out += f"\n             next     | {self.suggested_command}"
        return out


CHECKS: list[tuple[str, str, Callable]] = []


def check(check_id: str, default_severity: str):
    """Register a check function in the CHECKS registry."""

    def wrapper(fn):
        CHECKS.append((check_id, default_severity, fn))
        return fn

    return wrapper


# ----------------------------------------------------------------- parsing helpers

_IPCONFIG_FIELDS = {
    "ip": r"IP(?:v4)? Address[\s.]*:\s*(\d+\.\d+\.\d+\.\d+)",
    "mask": r"Subnet Mask[\s.]*:\s*(\d+\.\d+\.\d+\.\d+)",
    "gateway": r"Default Gateway[\s.]*:\s*(\d+\.\d+\.\d+\.\d+)",
    "dns": r"DNS Servers?[\s.]*:\s*(\d+\.\d+\.\d+\.\d+)",
}

_INTF_BRIEF_RE = re.compile(
    r"^(?P<intf>[A-Za-z]\S*)\s+"
    r"(?P<ip>\d+\.\d+\.\d+\.\d+|unassigned)\s+"
    r"(?P<ok>YES|NO)\s+"
    r"(?P<method>\S+)\s+"
    r"(?P<status>administratively down|up|down)\s+"
    r"(?P<proto>up|down)\s*$",
    re.MULTILINE,
)

_ROUTE_RE = re.compile(
    r"^\s*(?P<code>S\*|[CSLODIB]\*?)\s+"
    r"(?P<net>\d+\.\d+\.\d+\.\d+)"
    r"(?:/(?P<prefix>\d+))?",
    re.MULTILINE,
)

_VLAN_BRIEF_RE = re.compile(
    r"^(?P<vid>\d+)\s+(?P<name>\S+)\s+(?P<status>active|act/unsup|suspended)",
    re.MULTILINE,
)

_TRUNK_ROW_RE = re.compile(
    r"^(?P<port>\S+)\s+(?P<mode>on|off|auto|desirable)\s+"
    r"(?P<encap>802\.1q|isl|negotiated|n-802\.1q)\s+"
    r"(?P<status>\S+)\s+(?P<native>\d+)\s*$",
    re.MULTILINE,
)

_ACL_ENTRY_RE = re.compile(
    r"^\s*(?P<seq>\d+)\s+(?P<action>permit|deny)\s+(?P<body>.*?)"
    r"(?:\s+\((?P<matches>\d+)\s+match(?:es)?\))?\s*$",
    re.MULTILINE,
)


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _find_line(text: str, needle: str) -> str:
    """Return the first line containing `needle`, for use as Finding evidence."""
    for line in _lines(text):
        if needle in line:
            return line
    return needle


def _ip_int(ip: str) -> int:
    parts = [int(p) for p in ip.split(".")]
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def _mask_prefix(mask: str) -> int:
    return bin(_ip_int(mask)).count("1")


def _same_subnet(ip_a: str, ip_b: str, mask: str) -> bool:
    m = _ip_int(mask)
    return (_ip_int(ip_a) & m) == (_ip_int(ip_b) & m)


def _in_wildcard(ip: str, network: str, wildcard: str) -> bool:
    """True if `ip` falls inside network/wildcard (Cisco ACL style)."""
    inv = ~_ip_int(wildcard) & 0xFFFFFFFF
    return (_ip_int(ip) & inv) == (_ip_int(network) & inv)


def host_config(text: str) -> dict:
    """Pull IP / mask / gateway / DNS out of an ipconfig block."""
    out = {}
    for key, pattern in _IPCONFIG_FIELDS.items():
        m = re.search(pattern, text)
        if m:
            out[key] = m.group(1)
    return out


def interface_table(text: str) -> list[dict]:
    """Parse every `show ip interface brief` row in the transcript."""
    return [m.groupdict() for m in _INTF_BRIEF_RE.finditer(text)]


def routes(text: str) -> list[dict]:
    """Parse route entries. Missing prefix defaults to /32 as the safest assumption."""
    out = []
    for m in _ROUTE_RE.finditer(text):
        d = m.groupdict()
        out.append(
            {
                "code": d["code"],
                "net": d["net"],
                "prefix": int(d["prefix"]) if d["prefix"] else 32,
                "line": m.group(0).strip(),
            }
        )
    return out


def device_blocks(text: str) -> dict[str, str]:
    """
    Split a transcript into per-device sections keyed on the CLI prompt.

    Needed by any check that compares two ends of a link (native VLAN, duplex, OSPF
    area) -- without this the checker cannot tell SW1's output from SW2's.
    """
    blocks: dict[str, list[str]] = {}
    current = "UNKNOWN"
    prompt_re = re.compile(r"^([A-Za-z][\w\-]*)(?:#|>\s)")

    for line in _lines(text):
        m = prompt_re.match(line)
        if m:
            current = m.group(1)
        blocks.setdefault(current, []).append(line)

    return {dev: "\n".join(body) for dev, body in blocks.items()}


_PING_CMD_RE = re.compile(r"ping\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)

_PING_FAIL_RE = re.compile(
    r"Request timed out"
    r"|Destination host unreachable"
    r"|Destination net unreachable"
    r"|Success rate is 0 percent"
    r"|transmit failed"
    r"|request timed-out",
    re.IGNORECASE,
)

_PING_OK_RE = re.compile(
    r"Reply from|Success rate is (?:100|[1-9]\d?) percent", re.IGNORECASE
)


def ping_results(text: str) -> list[dict]:
    """
    Extract ping attempts and whether they succeeded.

    Two subtleties that both caused wrong findings before they were handled:

    1. `Reply from 192.168.10.1: Destination host unreachable.` contains "Reply from" but
       is a FAILURE -- the gateway is answering on the target's behalf to say it cannot
       route the packet. Failure markers therefore take precedence over success markers.

    2. The result window must stop at the next ping command. A fixed lookahead let one
       ping's "Request timed out" be attributed to the previous, successful ping.
    """
    out = []
    lines = _lines(text)
    ping_line_indexes = [i for i, ln in enumerate(lines) if _PING_CMD_RE.search(ln)]

    for pos, i in enumerate(ping_line_indexes):
        target = _PING_CMD_RE.search(lines[i]).group(1)

        # Stop the window at the next ping command, or 8 lines, whichever comes first.
        next_ping = (
            ping_line_indexes[pos + 1]
            if pos + 1 < len(ping_line_indexes)
            else len(lines)
        )
        end = min(i + 8, next_ping)
        window = "\n".join(lines[i:end])

        failed = bool(_PING_FAIL_RE.search(window))
        succeeded = bool(_PING_OK_RE.search(window)) and not failed

        out.append(
            {
                "target": target,
                "failed": failed,
                "succeeded": succeeded,
                "line": lines[i].strip(),
            }
        )
    return out


def _all_text(case: dict) -> str:
    return case.get("show_outputs", "")


# ------------------------------------------------------------ Layer 1 / physical


@check("interface_admin_down", "High")
def check_interface_admin_down(case) -> Iterator[Finding]:
    """An interface left in shutdown. Reported by IOS as 'administratively down'."""
    text = _all_text(case)
    for row in interface_table(text):
        if row["status"] == "administratively down":
            yield Finding(
                check_id="interface_admin_down",
                severity="High",
                message=(
                    f"{row['intf']} is administratively down -- the interface was never "
                    f"brought up with 'no shutdown'."
                ),
                evidence=_find_line(text, row["intf"]),
                suggested_command=f"configure terminal ; interface {row['intf']} ; no shutdown",
            )


@check("interface_protocol_down", "Medium")
def check_interface_protocol_down(case) -> Iterator[Finding]:
    """
    Line up but protocol down. Usually means the far end is shut or the encapsulation
    does not match -- the fault is not on this device.
    """
    text = _all_text(case)
    for row in interface_table(text):
        if row["status"] == "up" and row["proto"] == "down":
            yield Finding(
                check_id="interface_protocol_down",
                severity="Medium",
                message=(
                    f"{row['intf']} is up/down: the physical layer is fine but the line "
                    f"protocol is not. Suspect the far end of this link."
                ),
                evidence=_find_line(text, row["intf"]),
                suggested_command=f"show interfaces {row['intf']}",
            )


@check("duplex_mismatch", "Medium")
def check_duplex_mismatch(case) -> Iterator[Finding]:
    """Half-duplex on one end and full on the other. Confirmed by late collisions."""
    text = _all_text(case)
    duplex_by_device = {}

    for dev, body in device_blocks(text).items():
        m = re.search(r"(Full|Half)-duplex", body)
        if m:
            duplex_by_device[dev] = m.group(1)

    modes = set(duplex_by_device.values())
    if len(modes) > 1:
        detail = ", ".join(f"{d}={mode}-duplex" for d, mode in duplex_by_device.items())
        late = re.search(r"^\s*\d+\s+late collision.*$", text, re.MULTILINE)
        yield Finding(
            check_id="duplex_mismatch",
            severity="Medium",
            message=f"Duplex mismatch across the link ({detail}).",
            evidence=(late.group(0) if late else _find_line(text, "duplex")),
            suggested_command="configure terminal ; interface <uplink> ; duplex full",
        )


# ------------------------------------------------------------- Layer 2 / switching


@check("missing_vlan", "High")
def check_missing_vlan(case) -> Iterator[Finding]:
    """A port assigned to a VLAN that does not exist in the VLAN database."""
    text = _all_text(case)
    if "show vlan brief" not in text:
        return

    defined = {int(m.group("vid")) for m in _VLAN_BRIEF_RE.finditer(text)}
    if not defined:
        return

    for m in re.finditer(r"switchport access vlan (\d+)", text):
        vid = int(m.group(1))
        if vid not in defined:
            yield Finding(
                check_id="missing_vlan",
                severity="High",
                message=(
                    f"A port is assigned to VLAN {vid}, but VLAN {vid} is not in the VLAN "
                    f"database (defined: {sorted(defined)}). The port sits in an inactive VLAN."
                ),
                evidence=_find_line(text, f"switchport access vlan {vid}"),
                suggested_command=f"configure terminal ; vlan {vid}",
            )


@check("trunk_vlan_not_allowed", "High")
def check_trunk_vlan_not_allowed(case) -> Iterator[Finding]:
    """A VLAN active on the switch but pruned from the trunk's allowed list."""
    text = _all_text(case)
    if "Vlans allowed on trunk" not in text:
        return

    allowed: set[int] = set()
    capture = False
    evidence_line = ""
    for line in _lines(text):
        if "Vlans allowed on trunk" in line:
            capture = True
            continue
        if capture:
            m = re.match(r"^(\S+)\s+([\d,\-]+)\s*$", line)
            if m:
                evidence_line = line
                for part in m.group(2).split(","):
                    if "-" in part:
                        lo, hi = part.split("-")
                        allowed.update(range(int(lo), int(hi) + 1))
                    elif part.strip().isdigit():
                        allowed.add(int(part))
            elif line.strip() == "":
                capture = False

    if not allowed:
        return

    active = {
        int(m.group("vid"))
        for m in _VLAN_BRIEF_RE.finditer(text)
        if m.group("status") == "active" and int(m.group("vid")) < 1002
    }
    missing = sorted(active - allowed)
    if missing:
        yield Finding(
            check_id="trunk_vlan_not_allowed",
            severity="High",
            message=(
                f"VLAN(s) {missing} are active on this switch but not permitted on the "
                f"trunk (allowed: {sorted(allowed)}). Their frames are pruned at the uplink."
            ),
            evidence=evidence_line or _find_line(text, "Vlans allowed on trunk"),
            suggested_command=(
                "configure terminal ; interface <trunk> ; "
                f"switchport trunk allowed vlan add {','.join(str(v) for v in missing)}"
            ),
        )


@check("native_vlan_mismatch", "Medium")
def check_native_vlan_mismatch(case) -> Iterator[Finding]:
    """Different native VLANs on the two ends of an 802.1q trunk."""
    text = _all_text(case)
    natives = {}

    for dev, body in device_blocks(text).items():
        for m in _TRUNK_ROW_RE.finditer(body):
            natives[f"{dev} {m.group('port')}"] = int(m.group("native"))

    if len(set(natives.values())) > 1:
        detail = ", ".join(f"{k}=VLAN {v}" for k, v in natives.items())
        cdp = re.search(r"^.*NATIVE_VLAN_MISMATCH.*$", text, re.MULTILINE)
        yield Finding(
            check_id="native_vlan_mismatch",
            severity="Medium",
            message=(
                f"Native VLAN mismatch on the trunk ({detail}). Untagged traffic will leak "
                f"between VLANs and STP will behave inconsistently."
            ),
            evidence=(cdp.group(0).strip() if cdp else _find_line(text, "Native vlan")),
            suggested_command=(
                "configure terminal ; interface <trunk> ; switchport trunk native vlan <id>"
            ),
        )


@check("uplink_not_trunking", "High")
def check_uplink_not_trunking(case) -> Iterator[Finding]:
    """
    `show interfaces trunk` returned nothing while multiple VLANs are defined, and an
    inter-switch link is in static access mode.
    """
    text = _all_text(case)
    if "show interfaces trunk" not in text:
        return

    has_trunk_rows = bool(_TRUNK_ROW_RE.search(text))
    access_mode = re.search(r"Operational Mode:\s*static access", text)
    if has_trunk_rows or not access_mode:
        return

    vlans = {
        int(m.group("vid"))
        for m in _VLAN_BRIEF_RE.finditer(text)
        if int(m.group("vid")) not in (1,) and int(m.group("vid")) < 1002
    }
    switchport_access = re.search(r"switchport mode access", text)
    if switchport_access or vlans:
        yield Finding(
            check_id="uplink_not_trunking",
            severity="High",
            message=(
                "No trunk interfaces are operational, yet the link is in static access "
                "mode. An inter-switch uplink left in access mode carries VLAN 1 only."
            ),
            evidence=_find_line(text, "Operational Mode"),
            suggested_command=(
                "configure terminal ; interface <uplink> ; switchport mode trunk"
            ),
        )


# --------------------------------------------------------- Layer 3 / addressing


def running_config_addresses(text: str) -> dict[str, str]:
    """
    Map interface name -> configured address by walking `interface X` blocks in a
    running-config dump.

    Needed so duplicate_ip can tell "this address appears in both show ip interface
    brief and running-config for the SAME interface" (normal) from "two different
    interfaces claim this address" (a real fault).
    """
    out: dict[str, str] = {}
    current = None
    for line in _lines(text):
        m = re.match(r"^interface (\S+)\s*$", line.strip())
        if m:
            current = m.group(1)
            continue
        if current:
            addr = re.match(
                r"^\s*ip address (\d+\.\d+\.\d+\.\d+) \d+\.\d+\.\d+\.\d+\s*$", line
            )
            if addr:
                out[current] = addr.group(1)
                current = None
    return out


def _canonical_intf(name: str) -> str:
    """
    Normalise Cisco interface abbreviations so Gi0/1 and GigabitEthernet0/1 compare
    equal. Without this, duplicate_ip fires on every router whose show output and
    running-config use different abbreviations for the same interface.
    """
    expansions = {
        "gi": "GigabitEthernet",
        "gig": "GigabitEthernet",
        "fa": "FastEthernet",
        "eth": "Ethernet",
        "se": "Serial",
        "s": "Serial",
        "vl": "Vlan",
    }
    m = re.match(r"^([A-Za-z]+)([\d/.]+)$", name)
    if not m:
        return name
    word, numbers = m.group(1).lower(), m.group(2)
    for abbrev, full in sorted(expansions.items(), key=lambda kv: -len(kv[0])):
        if word.startswith(abbrev) and full.lower().startswith(word):
            return full + numbers
    return name[0].upper() + name[1:]


@check("duplicate_ip", "High")
def check_duplicate_ip(case) -> Iterator[Finding]:
    """
    The same address configured on two different interfaces.

    No case in the shipped dataset contains a duplicate address, so this check is
    exercised by tests/test_rule_checker.py rather than by cases.csv.
    """
    text = _all_text(case)
    owners: dict[str, set[str]] = {}

    for row in interface_table(text):
        if row["ip"] != "unassigned":
            owners.setdefault(row["ip"], set()).add(_canonical_intf(row["intf"]))

    for intf, ip in running_config_addresses(text).items():
        owners.setdefault(ip, set()).add(_canonical_intf(intf))

    for ip, intfs in owners.items():
        if len(intfs) > 1:
            yield Finding(
                check_id="duplicate_ip",
                severity="High",
                message=(
                    f"Address {ip} is configured on more than one interface "
                    f"({sorted(intfs)}). Only one can be active."
                ),
                evidence=_find_line(text, ip),
                suggested_command=f"show ip interface brief | include {ip}",
            )


@check("mask_mismatch", "High")
def check_mask_mismatch(case) -> Iterator[Finding]:
    """
    A host mask that disagrees with the mask on its own gateway's interface. Produces
    the classic "some hosts reachable, others not" symptom.
    """
    text = _all_text(case)
    host = host_config(text)
    if "ip" not in host or "mask" not in host:
        return

    host_prefix = _mask_prefix(host["mask"])

    for m in re.finditer(r"ip address (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)", text):
        gw_ip, gw_mask = m.group(1), m.group(2)
        if not _same_subnet(host["ip"], gw_ip, gw_mask):
            continue
        gw_prefix = _mask_prefix(gw_mask)
        if gw_prefix != host_prefix:
            yield Finding(
                check_id="mask_mismatch",
                severity="High",
                message=(
                    f"Host mask /{host_prefix} does not match the /{gw_prefix} configured "
                    f"on the gateway interface for the same segment."
                ),
                evidence=_find_line(text, host["mask"]),
                suggested_command="show running-config interface <gateway-interface>",
            )
            return


@check("gateway_not_in_subnet", "High")
def check_gateway_not_in_subnet(case) -> Iterator[Finding]:
    """A default gateway outside the host's own subnet is unreachable by definition."""
    text = _all_text(case)
    host = host_config(text)
    if not {"ip", "mask", "gateway"} <= set(host):
        return

    if not _same_subnet(host["ip"], host["gateway"], host["mask"]):
        yield Finding(
            check_id="gateway_not_in_subnet",
            severity="High",
            message=(
                f"Default gateway {host['gateway']} is outside the host's own subnet "
                f"({host['ip']}/{_mask_prefix(host['mask'])}). The host has no route to it."
            ),
            evidence=_find_line(text, host["gateway"]),
            suggested_command="ipconfig /all",
        )


@check("gateway_not_a_router_interface", "Medium")
def check_gateway_not_a_router_interface(case) -> Iterator[Finding]:
    """
    The gateway is in the right subnet but does not match any router interface in the
    transcript. Almost always a typo like .254 for .1.
    """
    text = _all_text(case)
    host = host_config(text)
    if not {"ip", "mask", "gateway"} <= set(host):
        return

    if not _same_subnet(host["ip"], host["gateway"], host["mask"]):
        return  # handled by gateway_not_in_subnet

    router_ips = {r["ip"] for r in interface_table(text) if r["ip"] != "unassigned"}
    router_ips.update(
        m.group(1)
        for m in re.finditer(r"ip address (\d+\.\d+\.\d+\.\d+) \d+\.\d+\.\d+\.\d+", text)
    )
    if not router_ips:
        return

    same_segment = {
        ip for ip in router_ips if _same_subnet(ip, host["ip"], host["mask"])
    }
    if same_segment and host["gateway"] not in router_ips:
        yield Finding(
            check_id="gateway_not_a_router_interface",
            severity="Medium",
            message=(
                f"Configured gateway {host['gateway']} does not match any router interface "
                f"in this transcript. The router address on this segment is "
                f"{sorted(same_segment)[0]}."
            ),
            evidence=_find_line(text, host["gateway"]),
            suggested_command="show ip interface brief",
        )


@check("subinterface_missing_for_vlan", "High")
def check_subinterface_missing_for_vlan(case) -> Iterator[Finding]:
    """
    Router-on-a-stick with a VLAN that has no matching subinterface, so that VLAN has
    no Layer 3 gateway at all.
    """
    text = _all_text(case)
    subif_vlans = {int(m.group(1)) for m in re.finditer(r"encapsulation dot1Q (\d+)", text)}
    subif_vlans.update(
        int(m.group(1)) for m in re.finditer(r"GigabitEthernet\d+/\d+\.(\d+)", text)
    )
    if not subif_vlans:
        return

    wanted: set[int] = set()
    for m in re.finditer(r"Vlans allowed on trunk", text):
        pass
    for line in _lines(text):
        m = re.match(r"^(\S+)\s+([\d,\-]+)\s*$", line)
        if m and "." not in m.group(1):
            for part in m.group(2).split(","):
                if part.strip().isdigit():
                    wanted.add(int(part))
    wanted.update(
        int(m.group(1))
        for m in re.finditer(r"Interface/VLAN mapping\s*:\s*VLAN (\d+)", text)
    )
    wanted.discard(1)

    missing = sorted(v for v in wanted - subif_vlans if v < 1002)
    if missing:
        yield Finding(
            check_id="subinterface_missing_for_vlan",
            severity="High",
            message=(
                f"VLAN(s) {missing} are carried in the topology but have no router "
                f"subinterface (present: {sorted(subif_vlans)}). Those VLANs have no gateway."
            ),
            evidence=_find_line(text, "encapsulation dot1Q"),
            suggested_command=(
                f"configure terminal ; interface GigabitEthernet0/0.{missing[0]} ; "
                f"encapsulation dot1Q {missing[0]}"
            ),
        )


@check("apipa_detected", "Medium")
def check_apipa_detected(case) -> Iterator[Finding]:
    """
    A 169.254.0.0/16 address means the host asked for DHCP and nothing answered. This
    is a symptom rather than a cause, but it narrows the search decisively.
    """
    text = _all_text(case)
    m = re.search(r"(169\.254\.\d+\.\d+)", text)
    if m:
        yield Finding(
            check_id="apipa_detected",
            severity="Medium",
            message=(
                f"Host holds APIPA address {m.group(1)}, so its DHCP DISCOVER went "
                f"unanswered. Look for an absent scope, an absent relay, or an exhausted pool "
                f"before suspecting anything at Layer 2."
            ),
            evidence=_find_line(text, m.group(1)),
            suggested_command="show ip dhcp pool",
        )


# ---------------------------------------------------------------- Layer 3 / routing


@check("no_route_to_destination", "High")
def check_no_route_to_destination(case) -> Iterator[Finding]:
    """
    A failing ping target not covered by any route, with no default route present.

    Only runs against an unfiltered `show ip route`. Output from `show ip route static`
    or `show ip route connected` is a partial table, and treating it as complete
    produced a confidently wrong finding on NS-018 (where the real fault is a bad
    next-hop, not a missing route).
    """
    text = _all_text(case)
    if not re.search(r"#\s*show ip route\s*$", text, re.MULTILINE):
        return

    table = routes(text)
    if not table:
        return

    has_default = any(r["net"] == "0.0.0.0" for r in table)

    for attempt in ping_results(text):
        if not attempt["failed"]:
            continue
        target = attempt["target"]
        covered = False
        for r in table:
            mask = (0xFFFFFFFF << (32 - r["prefix"])) & 0xFFFFFFFF
            if (_ip_int(target) & mask) == (_ip_int(r["net"]) & mask):
                covered = True
                break
        if not covered and not has_default:
            yield Finding(
                check_id="no_route_to_destination",
                severity="High",
                message=(
                    f"No route covers {target} and no default route (0.0.0.0/0) is present, "
                    f"so the router drops the traffic itself."
                ),
                evidence=attempt["line"],
                suggested_command=f"show ip route {target}",
            )
            return


@check("static_route_nexthop_unreachable", "High")
def check_static_route_nexthop_unreachable(case) -> Iterator[Finding]:
    """
    A static route whose next-hop address does not answer ping. The route installs
    happily -- IOS does not validate the next-hop exists -- so the table looks correct
    while traffic goes nowhere.
    """
    text = _all_text(case)
    nexthops = re.findall(
        r"^\s*S\*?\s+\d+\.\d+\.\d+\.\d+(?:/\d+)?\s+\[[\d/]+\]\s+via (\d+\.\d+\.\d+\.\d+)",
        text,
        re.MULTILINE,
    )
    if not nexthops:
        return

    failed_targets = {a["target"] for a in ping_results(text) if a["failed"]}

    for hop in dict.fromkeys(nexthops):
        if hop in failed_targets:
            yield Finding(
                check_id="static_route_nexthop_unreachable",
                severity="High",
                message=(
                    f"A static route points to next-hop {hop}, which does not respond to "
                    f"ping. IOS installs a static route without validating the next-hop, so "
                    f"the routing table looks correct while the traffic is black-holed."
                ),
                evidence=_find_line(text, f"via {hop}"),
                suggested_command="show cdp neighbors detail | include IP address",
            )
            return


@check("route_mask_suspicious", "Medium")
def check_route_mask_suspicious(case) -> Iterator[Finding]:
    """
    A static route whose prefix is longer than the connected prefix for the same network
    on the remote router -- classic /26-for-/24 typo, reachable for low hosts only.
    """
    text = _all_text(case)
    table = routes(text)
    static = {r["net"]: r for r in table if r["code"].startswith("S")}
    connected = {r["net"]: r for r in table if r["code"].startswith("C")}

    for net, s in static.items():
        c = connected.get(net)
        if c and s["prefix"] > c["prefix"]:
            yield Finding(
                check_id="route_mask_suspicious",
                severity="Medium",
                message=(
                    f"Static route for {net} uses /{s['prefix']} but the network is actually "
                    f"/{c['prefix']}. Only the first portion of the remote LAN is reachable."
                ),
                evidence=s["line"],
                suggested_command=f"show ip route {net}",
            )


@check("ospf_area_mismatch", "High")
def check_ospf_area_mismatch(case) -> Iterator[Finding]:
    """Two routers advertising the same link into different OSPF areas never peer."""
    text = _all_text(case)
    per_device: dict[str, dict[str, str]] = {}

    for dev, body in device_blocks(text).items():
        for m in re.finditer(
            r"network (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+) area (\d+)", body
        ):
            net, wc, area = m.groups()
            per_device.setdefault(f"{net} {wc}", {})[dev] = area

    for link, areas in per_device.items():
        if len(set(areas.values())) > 1:
            detail = ", ".join(f"{d}=area {a}" for d, a in areas.items())
            yield Finding(
                check_id="ospf_area_mismatch",
                severity="High",
                message=(
                    f"Network {link} is advertised into different OSPF areas ({detail}). "
                    f"Area IDs must match on a shared segment for adjacency to form."
                ),
                evidence=_find_line(text, f"network {link.split()[0]}"),
                suggested_command="show ip ospf interface | include Area",
            )
            return


# ------------------------------------------------------------------------- ACL


def _acl_blocks(text: str) -> dict[str, list[dict]]:
    """Group ACL entries under their parent access-list name."""
    blocks: dict[str, list[dict]] = {}
    current = None
    for line in _lines(text):
        header = re.match(
            r"^(?:Standard|Extended) IP access list (\S+)", line.strip()
        )
        if header:
            current = header.group(1)
            blocks[current] = []
            continue
        if current:
            m = _ACL_ENTRY_RE.match(line)
            if m:
                d = m.groupdict()
                blocks[current].append(
                    {
                        "seq": int(d["seq"]),
                        "action": d["action"],
                        "body": d["body"].strip(),
                        "matches": int(d["matches"]) if d["matches"] else None,
                        "line": line.strip(),
                    }
                )
            elif line.strip() and not line.startswith(" "):
                current = None
    return blocks


@check("acl_applied_wrong_direction", "High")
def check_acl_applied_wrong_direction(case) -> Iterator[Finding]:
    """
    An ACL that filters by LAN source but is applied outbound on the LAN interface. It
    then evaluates traffic heading toward the hosts instead of traffic from them.
    """
    text = _all_text(case)
    for m in re.finditer(r"ip access-group (\S+) (in|out)", text):
        acl_name, direction = m.group(1), m.group(2)
        if direction != "out":
            continue
        entries = _acl_blocks(text).get(acl_name, [])
        permits = [e for e in entries if e["action"] == "permit"]
        if not permits:
            continue
        zero_permits = all(e["matches"] == 0 for e in permits if e["matches"] is not None)
        denies_hit = any(
            e["action"] == "deny" and (e["matches"] or 0) > 0 for e in entries
        )
        if zero_permits and denies_hit:
            yield Finding(
                check_id="acl_applied_wrong_direction",
                severity="High",
                message=(
                    f"ACL {acl_name} is applied outbound, its permit entries have zero "
                    f"matches, and its deny is matching heavily. The direction is inverted -- "
                    f"it should almost certainly be applied inbound."
                ),
                evidence=_find_line(text, f"ip access-group {acl_name} {direction}"),
                suggested_command=(
                    f"configure terminal ; interface <lan> ; no ip access-group {acl_name} out ; "
                    f"ip access-group {acl_name} in"
                ),
            )


@check("acl_shadowed_entry", "High")
def check_acl_shadowed_entry(case) -> Iterator[Finding]:
    """A permit that can never match because a broader deny sits above it."""
    text = _all_text(case)
    for name, entries in _acl_blocks(text).items():
        for i, entry in enumerate(entries):
            if entry["action"] != "permit" or entry["matches"] != 0:
                continue
            for earlier in entries[:i]:
                if earlier["action"] != "deny" or not (earlier["matches"] or 0) > 0:
                    continue
                if _bodies_overlap(earlier["body"], entry["body"]):
                    yield Finding(
                        check_id="acl_shadowed_entry",
                        severity="High",
                        message=(
                            f"In ACL {name}, permit at sequence {entry['seq']} has zero "
                            f"matches and is shadowed by the broader deny at sequence "
                            f"{earlier['seq']}. Entries are evaluated top down, so the permit "
                            f"is unreachable."
                        ),
                        evidence=entry["line"],
                        suggested_command=(
                            f"configure terminal ; ip access-list extended {name} ; "
                            f"no {entry['seq']} ; {earlier['seq'] - 1} {entry['action']} "
                            f"{entry['body']}"
                        ),
                    )
                    return


def _bodies_overlap(deny_body: str, permit_body: str) -> bool:
    """
    Crude containment test: do the deny and permit reference the same network or host?

    Deliberately conservative. A false negative here just means the checker stays quiet
    and the AI layer carries the case; a false positive would put a wrong finding in
    front of the reviewer, which is worse.
    """
    deny_ips = set(re.findall(r"\d+\.\d+\.\d+\.\d+", deny_body))
    permit_ips = set(re.findall(r"\d+\.\d+\.\d+\.\d+", permit_body))
    if deny_ips & permit_ips:
        return True

    for d in deny_ips:
        for p in permit_ips:
            if d.rsplit(".", 1)[0] == p.rsplit(".", 1)[0]:
                return True
    return "any" in deny_body and bool(permit_ips)


@check("acl_implicit_deny_gap", "High")
def check_acl_implicit_deny_gap(case) -> Iterator[Finding]:
    """
    An ACL permitting only a couple of TCP ports with no explicit terminating deny. The
    implicit deny then silently drops DNS, DHCP and ICMP.
    """
    text = _all_text(case)
    applied = {m.group(1) for m in re.finditer(r"ip access-group (\S+) (?:in|out)", text)}

    for name, entries in _acl_blocks(text).items():
        if name not in applied or not entries:
            continue
        if any(e["action"] == "deny" for e in entries):
            continue

        permitted_protocols = {
            e["body"].split()[0] for e in entries if e["action"] == "permit" and e["body"]
        }
        if permitted_protocols and permitted_protocols <= {"tcp"}:
            yield Finding(
                check_id="acl_implicit_deny_gap",
                severity="High",
                message=(
                    f"ACL {name} permits TCP only and has no explicit deny, so the implicit "
                    f"'deny ip any any' drops UDP 53 (DNS), UDP 67/68 (DHCP) and ICMP."
                ),
                evidence=entries[-1]["line"],
                suggested_command=(
                    f"configure terminal ; ip access-list extended {name} ; "
                    f"permit udp any any eq 53 ; permit udp any any eq 67"
                ),
            )


@check("acl_zero_matches", "Critical")
def check_acl_zero_matches(case) -> Iterator[Finding]:
    """
    Every entry in an applied ACL shows zero matches while the traffic it targets is
    demonstrably flowing. The ACL is attached somewhere the traffic never passes.
    """
    text = _all_text(case)
    applied = dict(re.findall(r"ip access-group (\S+) (in|out)", text))

    traffic_flowing = bool(re.search(r"Reply from", text))
    if not traffic_flowing:
        return

    for name, entries in _acl_blocks(text).items():
        if name not in applied or not entries:
            continue
        counted = [e for e in entries if e["matches"] is not None]
        if not counted or len(counted) < len(entries):
            continue
        if all(e["matches"] == 0 for e in counted):
            yield Finding(
                check_id="acl_zero_matches",
                severity="Critical",
                message=(
                    f"Every entry in applied ACL {name} has zero matches, yet traffic is "
                    f"reaching its destination. The ACL is attached to an interface or "
                    f"direction the traffic never traverses -- the filter is not being enforced."
                ),
                evidence=entries[0]["line"],
                suggested_command="show running-config | include ip access-group",
            )


# ------------------------------------------------------------------------- NAT


def _nat_role_listed(text: str, role: str) -> bool:
    """
    True when `show ip nat statistics` actually names an interface for the given role.

    IOS prints this two ways and the guard has to accept both, because getting it wrong
    breaks in opposite directions:

        Inside interfaces: GigabitEthernet0/0     <- inline (NS-028)

        Inside interfaces:                        <- on the following indented line
          GigabitEthernet0/0

    An empty header followed by the next unindented field is NOT a listing (NS-026):

        Inside interfaces:
        Hits: 0  Misses: 0

    History, because this guard has been wrong twice. The first version used `\\s*` to
    skip to the interface name; `\\s` matches newlines, so on NS-026 it crossed the line
    boundary, captured `Hits: 0  Misses: 0` as the interface list and suppressed a true
    finding. Tightening it to `[ \\t]*` fixed that but then rejected the indented form
    above, which is what real devices print most often.
    """
    m = re.search(rf"^[ \t]*{role} interfaces:[ \t]*(.*)$", text, re.MULTILINE)
    if not m:
        return False

    if m.group(1).strip():
        return True

    for line in text[m.end():].splitlines():
        if not line.strip():
            continue
        indented = line[:1] in (" ", "\t")
        another_field = re.match(r"^[ \t]*(Hits|Misses|Expired|Inside|Outside|Total)\b", line)
        return bool(indented and not another_field)

    return False


@check("nat_roles_missing", "High")
def check_nat_roles_missing(case) -> Iterator[Finding]:
    """
    NAT rule configured but no interface marked 'ip nat inside' or 'ip nat outside'.

    `show running-config | include ip nat` filters out the per-interface role commands,
    so their absence from the transcript does not prove they are absent from the device.
    When `show ip nat statistics` lists populated inside/outside interfaces, that is
    authoritative evidence the roles exist and this check stays quiet -- otherwise it
    reports a missing-role fault on every case that used the filtered form of the
    command (this was a false positive on NS-028).
    """
    text = _all_text(case)
    if "ip nat inside source" not in text:
        return

    if _nat_role_listed(text, "Inside") and _nat_role_listed(text, "Outside"):
        return

    has_inside = re.search(r"^\s*ip nat inside\s*$", text, re.MULTILINE)
    has_outside = re.search(r"^\s*ip nat outside\s*$", text, re.MULTILINE)

    empty_lists = re.search(r"Outside interfaces:\s*\n\s*Inside interfaces:", text)

    if not (has_inside and has_outside):
        missing = []
        if not has_inside:
            missing.append("ip nat inside")
        if not has_outside:
            missing.append("ip nat outside")
        yield Finding(
            check_id="nat_roles_missing",
            severity="High",
            message=(
                f"A NAT translation rule exists but {' and '.join(missing)} is not "
                f"configured on any interface, so the rule never engages."
            ),
            evidence=(
                empty_lists.group(0).strip()
                if empty_lists
                else _find_line(text, "ip nat inside source")
            ),
            suggested_command=(
                "configure terminal ; interface <lan> ; ip nat inside ; "
                "interface <wan> ; ip nat outside"
            ),
        )


@check("nat_acl_coverage_gap", "High")
def check_nat_acl_coverage_gap(case) -> Iterator[Finding]:
    """An 'ip nat inside' subnet that the NAT source ACL does not permit."""
    text = _all_text(case)
    m = re.search(r"ip nat inside source list (\S+)", text)
    if not m:
        return
    acl_id = m.group(1)

    permits = re.findall(
        rf"access-list {re.escape(acl_id)} permit (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)",
        text,
    )
    if not permits:
        return

    inside_subnets = []
    current_ip = None
    for line in _lines(text):
        addr = re.search(r"ip address (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)", line)
        if addr:
            current_ip = addr.group(1)
        if re.match(r"^\s*ip nat inside\s*$", line) and current_ip:
            inside_subnets.append(current_ip)
            current_ip = None

    for ip in inside_subnets:
        if not any(_in_wildcard(ip, net, wc) for net, wc in permits):
            yield Finding(
                check_id="nat_acl_coverage_gap",
                severity="High",
                message=(
                    f"Interface with address {ip} is marked 'ip nat inside', but ACL "
                    f"{acl_id} does not permit its subnet. Traffic from that LAN leaves "
                    f"untranslated and is dropped upstream."
                ),
                evidence=_find_line(text, f"access-list {acl_id} permit"),
                suggested_command=(
                    f"configure terminal ; access-list {acl_id} permit "
                    f"{ip.rsplit('.', 1)[0]}.0 0.0.0.255"
                ),
            )
            return


@check("nat_missing_overload", "Medium")
def check_nat_missing_overload(case) -> Iterator[Finding]:
    """
    Interface-based NAT without 'overload'. With a single public address that permits
    exactly one concurrent inside host.
    """
    text = _all_text(case)
    m = re.search(r"ip nat inside source list \S+ interface \S+(?P<tail>.*)$", text, re.MULTILINE)
    if not m:
        return
    if "overload" in m.group("tail"):
        return

    yield Finding(
        check_id="nat_missing_overload",
        severity="Medium",
        message=(
            "NAT is configured against an interface without the 'overload' keyword, so the "
            "router performs one-to-one NAT instead of PAT. Only one inside host can "
            "translate at a time."
        ),
        evidence=_find_line(text, "ip nat inside source list"),
        suggested_command=(
            "configure terminal ; ip nat inside source list <acl> interface <wan> overload"
        ),
    )


# ------------------------------------------------------------------------ DHCP


@check("dhcp_pool_exhausted", "Medium")
def check_dhcp_pool_exhausted(case) -> Iterator[Finding]:
    """Leased address count equal to the pool total."""
    text = _all_text(case)
    total = re.search(r"Total addresses\s*:\s*(\d+)", text)
    leased = re.search(r"Leased addresses\s*:\s*(\d+)", text)
    if not (total and leased):
        return

    if int(leased.group(1)) >= int(total.group(1)) > 0:
        yield Finding(
            check_id="dhcp_pool_exhausted",
            severity="Medium",
            message=(
                f"DHCP pool is fully allocated ({leased.group(1)} of {total.group(1)} "
                f"leased). Further clients cannot obtain an address and will fall back to APIPA."
            ),
            evidence=_find_line(text, "Leased addresses"),
            suggested_command="show ip dhcp pool",
        )


@check("dhcp_pool_subnet_mismatch", "High")
def check_dhcp_pool_subnet_mismatch(case) -> Iterator[Finding]:
    """A pool network that does not match any interface the router serves."""
    text = _all_text(case)
    pool_nets = re.findall(
        r"^\s*network (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)\s*$", text, re.MULTILINE
    )
    if not pool_nets:
        return

    intf_ips = [r["ip"] for r in interface_table(text) if r["ip"] != "unassigned"]
    intf_ips += re.findall(r"ip address (\d+\.\d+\.\d+\.\d+) \d+\.\d+\.\d+\.\d+", text)
    if not intf_ips:
        return

    for net, mask in pool_nets:
        if not any(_same_subnet(net, ip, mask) for ip in intf_ips):
            yield Finding(
                check_id="dhcp_pool_subnet_mismatch",
                severity="High",
                message=(
                    f"DHCP pool network {net} {mask} does not match any interface subnet on "
                    f"this router (interfaces: {sorted(set(intf_ips))}). Clients receive "
                    f"addresses for a subnet that does not exist here."
                ),
                evidence=_find_line(text, f"network {net}"),
                suggested_command="show running-config | section dhcp",
            )
            return


@check("dhcp_default_router_invalid", "High")
def check_dhcp_default_router_invalid(case) -> Iterator[Finding]:
    """A pool handing out a default-router address that no interface owns."""
    text = _all_text(case)
    routers = re.findall(r"default-router (\d+\.\d+\.\d+\.\d+)", text)
    if not routers:
        return

    intf_ips = {r["ip"] for r in interface_table(text) if r["ip"] != "unassigned"}
    intf_ips.update(
        re.findall(r"ip address (\d+\.\d+\.\d+\.\d+) \d+\.\d+\.\d+\.\d+", text)
    )
    if not intf_ips:
        return

    for gw in routers:
        if gw not in intf_ips:
            yield Finding(
                check_id="dhcp_default_router_invalid",
                severity="High",
                message=(
                    f"DHCP pool advertises default-router {gw}, which is not an address on "
                    f"this router (interfaces: {sorted(intf_ips)}). Every client receives an "
                    f"unusable gateway."
                ),
                evidence=_find_line(text, f"default-router {gw}"),
                suggested_command="show running-config | section dhcp",
            )
            return


@check("dhcp_relay_missing", "High")
def check_dhcp_relay_missing(case) -> Iterator[Finding]:
    """
    One subinterface has ip helper-address and a sibling does not, while a host on the
    sibling holds an APIPA address.
    """
    text = _all_text(case)
    if "ip helper-address" not in text or "169.254." not in text:
        return

    subifs: dict[str, bool] = {}
    current = None
    for line in _lines(text):
        m = re.match(r"^interface (\S+\.\d+)\s*$", line.strip())
        if m:
            current = m.group(1)
            subifs[current] = False
        elif current and "ip helper-address" in line:
            subifs[current] = True

    without = [name for name, has in subifs.items() if not has]
    if without and any(subifs.values()):
        yield Finding(
            check_id="dhcp_relay_missing",
            severity="High",
            message=(
                f"Subinterface(s) {without} have no 'ip helper-address' while a sibling "
                f"subinterface does, and a client is on APIPA. DHCP broadcasts from those "
                f"VLANs are never relayed to the server."
            ),
            evidence=_find_line(text, "ip helper-address"),
            suggested_command=(
                f"configure terminal ; interface {without[0]} ; ip helper-address <server>"
            ),
        )


# ------------------------------------------------------------------------- DNS


@check("dns_server_unreachable", "Medium")
def check_dns_server_unreachable(case) -> Iterator[Finding]:
    """The configured DNS server address does not answer ICMP anywhere in the transcript."""
    text = _all_text(case)
    host = host_config(text)
    dns = host.get("dns")
    if not dns:
        return

    for attempt in ping_results(text):
        if attempt["target"] == dns and attempt["failed"]:
            yield Finding(
                check_id="dns_server_unreachable",
                severity="Medium",
                message=(
                    f"Configured DNS server {dns} does not respond to ping, so name "
                    f"resolution cannot work. Verify the address is correct before "
                    f"investigating the DNS service itself."
                ),
                evidence=attempt["line"],
                suggested_command=f"ping {dns}",
            )
            return


@check("dns_service_disabled", "High")
def check_dns_service_disabled(case) -> Iterator[Finding]:
    """The DNS daemon is switched off on the server. Host and records are irrelevant."""
    text = _all_text(case)
    m = re.search(r"^\s*DNS Service\s*:\s*Off\s*$", text, re.MULTILINE | re.IGNORECASE)
    if m:
        yield Finding(
            check_id="dns_service_disabled",
            severity="High",
            message=(
                "The DNS service is disabled on the server. The host is reachable and the "
                "zone records are present, but nothing is listening to answer queries."
            ),
            evidence=m.group(0).strip(),
            suggested_command="On the server: Services > DNS > set Service to On",
        )


@check("dns_record_points_nowhere", "Medium")
def check_dns_record_points_nowhere(case) -> Iterator[Finding]:
    """
    Name resolution succeeds but resolves to an address that nothing answers on. The
    zone is serving a stale or mistyped A record.
    """
    text = _all_text(case)
    failed_targets = {a["target"] for a in ping_results(text) if a["failed"]}
    if not failed_targets:
        return

    for m in re.finditer(
        r"^\s*(\S+)\s+A\s+(\d+\.\d+\.\d+\.\d+)\s*$", text, re.MULTILINE
    ):
        name, ip = m.group(1), m.group(2)
        if ip in failed_targets:
            yield Finding(
                check_id="dns_record_points_nowhere",
                severity="Medium",
                message=(
                    f"The A record for {name} resolves to {ip}, which does not respond to "
                    f"ping. Resolution is working correctly -- the record itself is wrong."
                ),
                evidence=m.group(0).strip(),
                suggested_command=f"nslookup {name}",
            )
            return


@check("switch_no_default_gateway", "Low")
def check_switch_no_default_gateway(case) -> Iterator[Finding]:
    """
    A Layer 2 switch with a management SVI but no `ip default-gateway`. It reaches its
    own subnet and nothing else.

    Assumes a /24 management subnet when comparing ping targets, since `show ip
    interface brief` does not report a mask.
    """
    text = _all_text(case)
    if "default-gateway" not in text:
        return
    if re.search(r"^\s*ip default-gateway \d+\.\d+\.\d+\.\d+", text, re.MULTILINE):
        return

    svis = [r for r in interface_table(text) if r["intf"].lower().startswith("vlan")]
    if not svis:
        return
    svi_ip = svis[0]["ip"]
    if svi_ip == "unassigned":
        return
    svi_prefix24 = svi_ip.rsplit(".", 1)[0]

    offnet_failed = [
        a
        for a in ping_results(text)
        if a["failed"] and a["target"].rsplit(".", 1)[0] != svi_prefix24
    ]
    onnet_ok = [
        a
        for a in ping_results(text)
        if a["succeeded"] and a["target"].rsplit(".", 1)[0] == svi_prefix24
    ]

    if offnet_failed and onnet_ok:
        yield Finding(
            check_id="switch_no_default_gateway",
            severity="Low",
            message=(
                f"Switch SVI {svis[0]['intf']} ({svi_ip}) reaches its own subnet but no "
                f"'ip default-gateway' is configured, so the switch cannot originate traffic "
                f"off-net. Management-plane only -- user traffic is unaffected."
            ),
            evidence=offnet_failed[0]["line"],
            suggested_command="configure terminal ; ip default-gateway <router-ip>",
        )


@check("wireless_security_mismatch", "Medium")
def check_wireless_security_mismatch(case) -> Iterator[Finding]:
    """Different authentication methods configured on the AP and the client."""
    text = _all_text(case)
    methods = re.findall(
        r"^\s*Authentication\s*:\s*(\S+)\s*$", text, re.MULTILINE
    )
    distinct = list(dict.fromkeys(methods))
    if len(distinct) > 1:
        auth_failed = re.search(r"^.*AUTH_FAILED.*$", text, re.MULTILINE)
        yield Finding(
            check_id="wireless_security_mismatch",
            severity="Medium",
            message=(
                f"Wireless authentication methods do not match across the link "
                f"({', '.join(distinct)}). Association cannot complete until both ends "
                f"agree."
            ),
            evidence=(
                auth_failed.group(0).strip()
                if auth_failed
                else _find_line(text, "Authentication")
            ),
            suggested_command="Set the client to match the AP: WPA2-PSK with AES",
        )


@check("wlan_vlan_mismatch", "Critical")
def check_wlan_vlan_mismatch(case) -> Iterator[Finding]:
    """
    A wireless client has landed inside the subnet that a filter is written to protect,
    rather than the subnet the filter treats as untrusted. The SSID is mapped to the
    wrong VLAN and the filter is bypassed entirely.
    """
    text = _all_text(case)
    host = host_config(text)
    client_ip = host.get("ip")
    if not client_ip or "Interface/VLAN mapping" not in text:
        return

    for m in re.finditer(
        r"deny ip (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+) "
        r"(\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)",
        text,
    ):
        src_net, src_wc, dst_net, dst_wc = m.groups()
        in_source = _in_wildcard(client_ip, src_net, src_wc)
        in_dest = _in_wildcard(client_ip, dst_net, dst_wc)

        if in_dest and not in_source:
            mapping = re.search(
                r"Interface/VLAN mapping\s*:\s*VLAN (\d+)", text
            )
            ssid = re.search(r"SSID:\s*(\S+)", text)
            yield Finding(
                check_id="wlan_vlan_mismatch",
                severity="Critical",
                message=(
                    f"Wireless client holds {client_ip}, which is inside the protected "
                    f"destination subnet {dst_net} rather than the untrusted source subnet "
                    f"{src_net}. SSID {ssid.group(1) if ssid else '<unknown>'} is mapped to "
                    f"VLAN {mapping.group(1) if mapping else '<unknown>'} -- the wrong VLAN. "
                    f"The filter is bypassed because the traffic never enters from the "
                    f"subnet it guards."
                ),
                evidence=_find_line(text, "Interface/VLAN mapping"),
                suggested_command="Remap the SSID to the guest VLAN, then re-test",
            )
            return


# ------------------------------------------------------------------------ runner


def run_checks(case: dict) -> list[Finding]:
    """Run every registered check against one case."""
    findings: list[Finding] = []
    for check_id, _severity, fn in CHECKS:
        try:
            result = fn(case)
            if result:
                findings.extend(result)
        except Exception as exc:  # a broken check must not sink the whole run
            findings.append(
                Finding(
                    check_id=check_id,
                    severity="Low",
                    message=f"check raised {type(exc).__name__}: {exc}",
                    evidence="",
                )
            )
    return findings


def check_count() -> int:
    return len(CHECKS)


def check_ids() -> list[str]:
    return [cid for cid, _, _ in CHECKS]
