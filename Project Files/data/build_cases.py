"""
build_cases.py -- generates data/cases.csv for NetSage AI.

The 33 troubleshooting cases below are hand-authored Cisco-style lab scenarios.
Each one pairs a symptom a student would actually report with the show-command
transcript they would collect, plus the known-correct fault for scoring.

This builder exists so the multi-line CLI transcripts get quoted correctly in the
CSV. Edit the CASES list here, re-run, and cases.csv is regenerated:

    python data/build_cases.py

Columns written:
    case_id, symptom, topology_note, show_outputs,
    expected_fault, osi_layer, concept_tag, severity
"""

import csv
import os

CASES = [
    # ---------------------------------------------------------------- VLAN (5)
    dict(
        case_id="NS-001",
        concept_tag="VLAN",
        osi_layer=2,
        severity="High",
        symptom=(
            "PC1 has a correct static IP and default gateway but cannot ping its own "
            "gateway. The switch port shows up/up and the link light is green."
        ),
        topology_note="PC1 -> SW1 Fa0/3 (access) -> R1 Gi0/0.20. PC1 = 192.168.20.10/24, GW 192.168.20.1.",
        show_outputs="""SW1#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Fa0/1, Fa0/2, Fa0/4, Fa0/5
10   SALES                            active    Fa0/6, Fa0/7
1002 fddi-default                     act/unsup
1003 token-ring-default               act/unsup

SW1#show interfaces Fa0/3 status
Port      Name    Status       Vlan   Duplex  Speed Type
Fa0/3             up           20     a-full  a-100 10/100BaseTX

SW1#show running-config interface Fa0/3
interface FastEthernet0/3
 switchport access vlan 20
 switchport mode access
end""",
        expected_fault=(
            "VLAN 20 was never created in the switch VLAN database. Fa0/3 is assigned to an "
            "inactive VLAN, so frames are dropped even though the port is up."
        ),
    ),
    dict(
        case_id="NS-002",
        concept_tag="VLAN",
        osi_layer=2,
        severity="Medium",
        symptom=(
            "PC5 receives 192.168.30.24 from DHCP but the lab sheet says it should be in the "
            "192.168.20.0/24 Sales subnet. It can reach other hosts, just the wrong ones."
        ),
        topology_note="PC5 -> SW1 Fa0/5. VLAN 20 = Sales (192.168.20.0/24), VLAN 30 = Guest (192.168.30.0/24).",
        show_outputs="""SW1#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Fa0/1, Fa0/2
20   SALES                            active    Fa0/4, Fa0/6
30   GUEST                            active    Fa0/5, Fa0/7

PC5> ipconfig /all
   IP Address. . . . . . . . . . . . : 192.168.30.24
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.30.1
   DHCP Server . . . . . . . . . . . : 192.168.30.1""",
        expected_fault=(
            "Fa0/5 is assigned to VLAN 30 (Guest) instead of VLAN 20 (Sales), so the host "
            "leases from the Guest DHCP scope."
        ),
    ),
    dict(
        case_id="NS-003",
        concept_tag="VLAN",
        osi_layer=2,
        severity="High",
        symptom=(
            "VLAN 40 hosts on SW1 can ping each other, but none of them can reach VLAN 40 "
            "hosts on SW2. VLAN 10 and 20 cross between the switches fine."
        ),
        topology_note="SW1 Gi0/1 <-> SW2 Gi0/1 trunk. VLANs 10, 20, 30, 40 defined on both switches.",
        show_outputs="""SW1#show interfaces trunk
Port        Mode         Encapsulation  Status        Native vlan
Gi0/1       on           802.1q         trunking      1

Port        Vlans allowed on trunk
Gi0/1       1,10,20,30

Port        Vlans allowed and active in management domain
Gi0/1       1,10,20,30

SW1#show vlan brief
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
40   ENGINEERING                      active    Fa0/8, Fa0/9""",
        expected_fault=(
            "VLAN 40 is missing from the trunk's allowed VLAN list on Gi0/1, so its frames are "
            "pruned at the inter-switch link."
        ),
    ),
    dict(
        case_id="NS-004",
        concept_tag="VLAN",
        osi_layer=2,
        severity="Medium",
        symptom=(
            "Connectivity between the two switches is intermittent and the console keeps "
            "printing a CDP warning every minute."
        ),
        topology_note="SW1 Fa0/24 <-> SW2 Fa0/24, 802.1q trunk. Management VLAN is 99.",
        show_outputs="""SW1#show interfaces trunk
Port        Mode         Encapsulation  Status        Native vlan
Fa0/24      on           802.1q         trunking      1

SW2#show interfaces trunk
Port        Mode         Encapsulation  Status        Native vlan
Fa0/24      on           802.1q         trunking      99

SW1#show logging | include CDP
%CDP-4-NATIVE_VLAN_MISMATCH: Native VLAN mismatch discovered on FastEthernet0/24 (1),
with SW2 FastEthernet0/24 (99).""",
        expected_fault=(
            "Native VLAN mismatch across the trunk: SW1 uses VLAN 1, SW2 uses VLAN 99. Untagged "
            "traffic leaks between VLANs and STP behaves inconsistently."
        ),
    ),
    dict(
        case_id="NS-005",
        concept_tag="VLAN",
        osi_layer=2,
        severity="High",
        symptom=(
            "After recabling, only VLAN 1 devices can talk across the switch-to-switch link. "
            "Every other VLAN is isolated to its own switch."
        ),
        topology_note="SW1 Gi0/1 <-> SW2 Gi0/1. VLANs 10, 20, 30 exist and are active on both.",
        show_outputs="""SW1#show interfaces trunk

SW1#show running-config interface Gi0/1
interface GigabitEthernet0/1
 switchport mode access
 spanning-tree portfast
end

SW1#show interfaces Gi0/1 switchport
Name: Gi0/1
Switchport: Enabled
Administrative Mode: static access
Operational Mode: static access
Access Mode VLAN: 1 (default)
Trunking Native Mode VLAN: 1 (default)""",
        expected_fault=(
            "The inter-switch link was left in static access mode in VLAN 1 instead of being "
            "configured as an 802.1q trunk, so only VLAN 1 crosses it."
        ),
    ),

    # ------------------------------------------------------------- Gateway (4)
    dict(
        case_id="NS-006",
        concept_tag="Gateway",
        osi_layer=3,
        severity="Medium",
        symptom=(
            "PC2 can ping every other host in its own subnet and can ping the router, but "
            "cannot reach anything in another subnet."
        ),
        topology_note="PC2 -> SW1 -> R1 Gi0/0 = 192.168.10.1/24. Remote LAN is 192.168.20.0/24.",
        show_outputs="""PC2> ipconfig /all
   IP Address. . . . . . . . . . . . : 192.168.10.20
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.10.254
   DNS Servers . . . . . . . . . . . : 192.168.10.5

PC2> ping 192.168.10.1
Reply from 192.168.10.1: bytes=32 time<1ms TTL=255

PC2> ping 192.168.20.10
Request timed out.
Request timed out.

R1#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     192.168.10.1    YES manual up                    up
GigabitEthernet0/1     192.168.20.1    YES manual up                    up""",
        expected_fault=(
            "PC2's default gateway is set to 192.168.10.254, which does not exist. The real "
            "gateway is 192.168.10.1."
        ),
    ),
    dict(
        case_id="NS-007",
        concept_tag="Gateway",
        osi_layer=3,
        severity="High",
        symptom=(
            "PC3 cannot ping its default gateway at all. Ping to hosts on the same subnet "
            "works normally."
        ),
        topology_note="PC3 -> SW2 -> R1 Gi0/0 = 192.168.10.1/24.",
        show_outputs="""PC3> ipconfig /all
   IP Address. . . . . . . . . . . . : 192.168.10.50
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.11.1

PC3> ping 192.168.10.51
Reply from 192.168.10.51: bytes=32 time<1ms TTL=128

PC3> ping 192.168.11.1
PING: transmit failed. General failure.

R1#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     192.168.10.1    YES manual up                    up""",
        expected_fault=(
            "The configured default gateway 192.168.11.1 is outside PC3's own 192.168.10.0/24 "
            "subnet, so the host has no route to reach it."
        ),
    ),
    dict(
        case_id="NS-008",
        concept_tag="Gateway",
        osi_layer=3,
        severity="Low",
        symptom=(
            "The switch management IP answers ping from a host in the management VLAN, but the "
            "switch cannot reach the syslog server on a different subnet."
        ),
        topology_note="SW3 SVI VLAN 99 = 10.0.99.3/24. Syslog server = 10.0.50.20. Gateway = 10.0.99.1.",
        show_outputs="""SW3#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
Vlan99                 10.0.99.3       YES manual up                    up

SW3#show running-config | include default-gateway

SW3#ping 10.0.50.20
Type escape sequence to abort.
Sending 5, 100-byte ICMP Echos to 10.0.50.20, timeout is 2 seconds:
.....
Success rate is 0 percent (0/5)

SW3#ping 10.0.99.1
Sending 5, 100-byte ICMP Echos to 10.0.99.1, timeout is 2 seconds:
!!!!!
Success rate is 100 percent (5/5)""",
        expected_fault=(
            "No 'ip default-gateway' is configured on the Layer 2 switch, so it can reach its "
            "local subnet but cannot originate traffic off-net."
        ),
    ),
    dict(
        case_id="NS-009",
        concept_tag="Gateway",
        osi_layer=3,
        severity="High",
        symptom=(
            "VLAN 30 hosts cannot ping their gateway 192.168.30.1. VLAN 10 and VLAN 20 hosts "
            "have full connectivity."
        ),
        topology_note="Router-on-a-stick: R1 Gi0/0 trunk to SW1, subinterfaces per VLAN.",
        show_outputs="""R1#show ip interface brief
Interface                  IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0         unassigned      YES unset  up                    up
GigabitEthernet0/0.10      192.168.10.1    YES manual up                    up
GigabitEthernet0/0.20      192.168.20.1    YES manual up                    up

R1#show running-config | section interface GigabitEthernet0/0
interface GigabitEthernet0/0.10
 encapsulation dot1Q 10
 ip address 192.168.10.1 255.255.255.0
interface GigabitEthernet0/0.20
 encapsulation dot1Q 20
 ip address 192.168.20.1 255.255.255.0

SW1#show interfaces trunk
Port        Vlans allowed on trunk
Gi0/1       1,10,20,30""",
        expected_fault=(
            "The router-on-a-stick configuration is missing the Gi0/0.30 subinterface for VLAN "
            "30, so that VLAN has no Layer 3 gateway."
        ),
    ),

    # ---------------------------------------------------------------- DHCP (4)
    dict(
        case_id="NS-010",
        concept_tag="DHCP",
        osi_layer=3,
        severity="Medium",
        symptom=(
            "Most PCs in the lab get an address, but the last few students to boot end up with "
            "169.254.x.x and no connectivity."
        ),
        topology_note="R1 acts as DHCP server for 192.168.10.0/24. Pool excludes .1 to .10.",
        show_outputs="""R1#show ip dhcp pool
Pool LAB_POOL :
 Utilization mark (high/low)    : 100 / 0
 Subnet size (first/next)       : 0 / 0
 Total addresses                : 244
 Leased addresses               : 244
 Pending event                  : none
 1 subnet is currently in the pool

R1#show ip dhcp binding | count Active
Number of lines which match regexp = 244

PC22> ipconfig
   IP Address. . . . . . . . . . . . : 169.254.88.17
   Subnet Mask . . . . . . . . . . . : 255.255.0.0
   Default Gateway . . . . . . . . . :""",
        expected_fault=(
            "The DHCP pool is fully exhausted (244 of 244 leased), so late clients fall back to "
            "APIPA addressing."
        ),
    ),
    dict(
        case_id="NS-011",
        concept_tag="DHCP",
        osi_layer=3,
        severity="High",
        symptom=(
            "Every PC on the 192.168.10.0/24 segment receives an address in 192.168.99.x and "
            "has no connectivity to anything."
        ),
        topology_note="R1 Gi0/0 = 192.168.10.1/24 serves DHCP for the directly attached LAN.",
        show_outputs="""R1#show running-config | section dhcp
ip dhcp excluded-address 192.168.10.1 192.168.10.10
ip dhcp pool LAN_POOL
 network 192.168.99.0 255.255.255.0
 default-router 192.168.10.1
 dns-server 192.168.10.5

R1#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     192.168.10.1    YES manual up                    up

PC1> ipconfig
   IP Address. . . . . . . . . . . . : 192.168.99.2
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.10.1""",
        expected_fault=(
            "The DHCP pool network statement is 192.168.99.0/24 but the serving interface is in "
            "192.168.10.0/24, so clients get addresses from the wrong subnet."
        ),
    ),
    dict(
        case_id="NS-012",
        concept_tag="DHCP",
        osi_layer=3,
        severity="High",
        symptom=(
            "All PCs get valid addresses in the right subnet and can ping each other, but none "
            "of them can reach another subnet."
        ),
        topology_note="R1 Gi0/0 = 192.168.10.1/24, DHCP server for the same LAN.",
        show_outputs="""R1#show running-config | section dhcp
ip dhcp pool LAN_POOL
 network 192.168.10.0 255.255.255.0
 default-router 192.168.10.254
 dns-server 192.168.10.5

R1#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     192.168.10.1    YES manual up                    up
GigabitEthernet0/1     203.0.113.2     YES manual up                    up

PC1> ipconfig /all
   IP Address. . . . . . . . . . . . : 192.168.10.11
   Default Gateway . . . . . . . . . : 192.168.10.254

PC1> ping 192.168.10.12
Reply from 192.168.10.12: bytes=32 time<1ms TTL=128""",
        expected_fault=(
            "The DHCP pool hands out default-router 192.168.10.254, which is not a real gateway. "
            "The router's LAN address is 192.168.10.1."
        ),
    ),
    dict(
        case_id="NS-013",
        concept_tag="DHCP",
        osi_layer=3,
        severity="High",
        symptom=(
            "VLAN 20 PCs never receive an address. VLAN 10 PCs, which share the subnet with the "
            "DHCP server, lease normally."
        ),
        topology_note="Dedicated DHCP server 192.168.10.5 in VLAN 10. R1 routes between VLAN 10 and 20.",
        show_outputs="""R1#show running-config | section interface GigabitEthernet0/0
interface GigabitEthernet0/0.10
 encapsulation dot1Q 10
 ip address 192.168.10.1 255.255.255.0
 ip helper-address 192.168.10.5
interface GigabitEthernet0/0.20
 encapsulation dot1Q 20
 ip address 192.168.20.1 255.255.255.0

PC-VLAN20> ipconfig
   IP Address. . . . . . . . . . . . : 169.254.12.9
   Subnet Mask . . . . . . . . . . . : 255.255.0.0

R1#debug ip dhcp server packet
DHCPD: DHCPDISCOVER received from interface GigabitEthernet0/0.10.
DHCPD: no DHCPDISCOVER received from GigabitEthernet0/0.20.""",
        expected_fault=(
            "Gi0/0.20 has no 'ip helper-address', so VLAN 20 DHCP broadcasts are never relayed "
            "to the server at 192.168.10.5."
        ),
    ),

    # ----------------------------------------------------------------- DNS (3)
    dict(
        case_id="NS-014",
        concept_tag="DNS",
        osi_layer=7,
        severity="Medium",
        symptom=(
            "Students can ping the web server by its IP address and can browse by IP, but any "
            "hostname fails to resolve."
        ),
        topology_note="DNS server runs on 192.168.10.5. Web server = 192.168.20.10 (www.lab.local).",
        show_outputs="""PC1> ipconfig /all
   IP Address. . . . . . . . . . . . : 192.168.10.30
   Default Gateway . . . . . . . . . : 192.168.10.1
   DNS Servers . . . . . . . . . . . : 192.168.10.53

PC1> ping 192.168.20.10
Reply from 192.168.20.10: bytes=32 time=1ms TTL=127

PC1> nslookup www.lab.local
Server:  192.168.10.53
Address: 192.168.10.53
DNS request timed out.
   timeout was 2 seconds.

PC1> ping 192.168.10.53
Request timed out.""",
        expected_fault=(
            "The host points at 192.168.10.53 as its DNS server, but the actual DNS service runs "
            "on 192.168.10.5. No device answers at .53."
        ),
    ),
    dict(
        case_id="NS-015",
        concept_tag="DNS",
        osi_layer=7,
        severity="High",
        symptom=(
            "Name resolution stopped working for the entire lab at once. IP connectivity is "
            "completely unaffected."
        ),
        topology_note="Single DNS server at 192.168.10.5, reachable from all VLANs.",
        show_outputs="""PC1> ping 192.168.10.5
Reply from 192.168.10.5: bytes=32 time<1ms TTL=128

PC1> nslookup www.lab.local
Server:  192.168.10.5
Address: 192.168.10.5
*** Request to 192.168.10.5 timed-out

SERVER-DNS  Services > DNS
   DNS Service : Off
   Resource Records:
     www.lab.local     A     192.168.20.10
     ftp.lab.local     A     192.168.20.11""",
        expected_fault=(
            "The DNS service is toggled Off on the server. The host and its records are fine, "
            "the daemon simply is not answering."
        ),
    ),
    dict(
        case_id="NS-016",
        concept_tag="DNS",
        osi_layer=7,
        severity="Medium",
        symptom=(
            "Browsing to www.lab.local times out, but the name does resolve and the web server "
            "itself serves pages fine when reached by IP."
        ),
        topology_note="Web server = 192.168.20.10. DNS server = 192.168.10.5.",
        show_outputs="""PC1> nslookup www.lab.local
Server:  192.168.10.5
Address: 192.168.10.5

Name:    www.lab.local
Address: 192.168.20.99

PC1> ping 192.168.20.99
Request timed out.

PC1> ping 192.168.20.10
Reply from 192.168.20.10: bytes=32 time=1ms TTL=127

SERVER-DNS  Services > DNS
   DNS Service : On
   Resource Records:
     www.lab.local     A     192.168.20.99""",
        expected_fault=(
            "The A record for www.lab.local points to 192.168.20.99, which is not assigned to "
            "any host. The web server is 192.168.20.10."
        ),
    ),

    # ------------------------------------------------------------- Routing (5)
    dict(
        case_id="NS-017",
        concept_tag="Routing",
        osi_layer=3,
        severity="High",
        symptom=(
            "Hosts behind R1 cannot reach the 192.168.30.0/24 LAN behind R2. The WAN link "
            "between the routers is up and the routers can ping each other."
        ),
        topology_note="R1 Gi0/1 = 10.0.0.1/30 <-> R2 Gi0/1 = 10.0.0.2/30. R2 serves 192.168.30.0/24.",
        show_outputs="""R1#show ip route
Codes: C - connected, S - static, L - local
     10.0.0.0/8 is variably subnetted, 2 subnets, 2 masks
C       10.0.0.0/30 is directly connected, GigabitEthernet0/1
L       10.0.0.1/32 is directly connected, GigabitEthernet0/1
C    192.168.10.0/24 is directly connected, GigabitEthernet0/0

R1#ping 10.0.0.2
Sending 5, 100-byte ICMP Echos to 10.0.0.2, timeout is 2 seconds:
!!!!!
Success rate is 100 percent (5/5)

PC1> ping 192.168.30.10
Reply from 192.168.10.1: Destination host unreachable.""",
        expected_fault=(
            "R1 has no route to 192.168.30.0/24 and no default route, so it drops the traffic "
            "and returns an ICMP unreachable."
        ),
    ),
    dict(
        case_id="NS-018",
        concept_tag="Routing",
        osi_layer=3,
        severity="High",
        symptom=(
            "A static route to 10.10.30.0/24 exists in the routing table but traffic to that "
            "network still fails."
        ),
        topology_note="R1 Gi0/1 = 10.0.0.1/30, peer R2 = 10.0.0.2/30. R2 owns 10.10.30.0/24.",
        show_outputs="""R1#show ip route static
     10.0.0.0/8 is variably subnetted, 3 subnets, 2 masks
S       10.10.30.0/24 [1/0] via 10.0.0.9

R1#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/1     10.0.0.1        YES manual up                    up

R1#show cdp neighbors detail | include IP address
  IP address: 10.0.0.2

R1#ping 10.0.0.9
Sending 5, 100-byte ICMP Echos to 10.0.0.9, timeout is 2 seconds:
.....
Success rate is 0 percent (0/5)""",
        expected_fault=(
            "The static route points to next-hop 10.0.0.9, which does not exist. The actual "
            "neighbour on the /30 link is 10.0.0.2."
        ),
    ),
    dict(
        case_id="NS-019",
        concept_tag="Routing",
        osi_layer=3,
        severity="High",
        symptom=(
            "All internal subnets reach each other, but nothing can reach the internet. Pings "
            "to 8.8.8.8 return 'Destination host unreachable' from the local router."
        ),
        topology_note="R1 Gi0/1 = 203.0.113.2/30 to ISP router 203.0.113.1.",
        show_outputs="""R1#show ip route
C    192.168.10.0/24 is directly connected, GigabitEthernet0/0
C    192.168.20.0/24 is directly connected, GigabitEthernet0/2
     203.0.113.0/30 is subnetted, 1 subnets
C       203.0.113.0 is directly connected, GigabitEthernet0/1

R1#ping 203.0.113.1
Sending 5, 100-byte ICMP Echos to 203.0.113.1, timeout is 2 seconds:
!!!!!
Success rate is 100 percent (5/5)

PC1> ping 8.8.8.8
Reply from 192.168.10.1: Destination host unreachable.""",
        expected_fault=(
            "No default route (0.0.0.0/0) toward the ISP is configured on R1, so packets for "
            "outside destinations have no matching route."
        ),
    ),
    dict(
        case_id="NS-020",
        concept_tag="Routing",
        osi_layer=3,
        severity="High",
        symptom=(
            "OSPF was configured on both routers but the neighbour relationship never forms and "
            "no OSPF routes appear."
        ),
        topology_note="R1 Gi0/1 = 10.0.0.1/30 <-> R2 Gi0/1 = 10.0.0.2/30.",
        show_outputs="""R1#show ip ospf neighbor

R1#show running-config | section router ospf
router ospf 1
 network 10.0.0.0 0.0.0.3 area 0
 network 192.168.10.0 0.0.0.255 area 0

R2#show running-config | section router ospf
router ospf 1
 network 10.0.0.0 0.0.0.3 area 1
 network 192.168.30.0 0.0.0.255 area 1

R1#show ip ospf interface Gi0/1 | include State|Area
  Internet Address 10.0.0.1/30, Area 0
  State DR, Priority 1

R1#ping 10.0.0.2
Success rate is 100 percent (5/5)""",
        expected_fault=(
            "The shared link is in OSPF area 0 on R1 but area 1 on R2. Area IDs must match on a "
            "common segment for adjacency to form."
        ),
    ),
    dict(
        case_id="NS-021",
        concept_tag="Routing",
        osi_layer=3,
        severity="Medium",
        symptom=(
            "Hosts 192.168.20.1 through .62 are reachable from R1, but anything from .70 upward "
            "times out. The route to that LAN is present."
        ),
        topology_note="R2 owns the full 192.168.20.0/24 LAN. R1 reaches it via a static route.",
        show_outputs="""R1#show ip route static
S    192.168.20.0/26 [1/0] via 10.0.0.2

R1#ping 192.168.20.50
Sending 5, 100-byte ICMP Echos to 192.168.20.50, timeout is 2 seconds:
!!!!!
Success rate is 100 percent (5/5)

R1#ping 192.168.20.80
Sending 5, 100-byte ICMP Echos to 192.168.20.80, timeout is 2 seconds:
.....
Success rate is 0 percent (0/5)

R2#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     192.168.20.1    YES manual up                    up
R2#show ip route connected
C    192.168.20.0/24 is directly connected, GigabitEthernet0/0""",
        expected_fault=(
            "The static route was written with mask 255.255.255.192 (/26) instead of /24, so only "
            "the first 62 hosts of the LAN are covered."
        ),
    ),

    # ----------------------------------------------------------------- ACL (4)
    dict(
        case_id="NS-022",
        concept_tag="ACL",
        osi_layer=4,
        severity="High",
        symptom=(
            "An ACL was added to allow only HTTP to the server, but now the entire LAN has lost "
            "all outbound connectivity."
        ),
        topology_note="R1 Gi0/0 faces the 192.168.10.0/24 LAN. Server = 192.168.20.10.",
        show_outputs="""R1#show access-lists
Extended IP access list 101
    10 permit tcp 192.168.10.0 0.0.0.255 host 192.168.20.10 eq www (0 matches)
    20 deny ip any any (1284 matches)

R1#show running-config interface GigabitEthernet0/0
interface GigabitEthernet0/0
 ip address 192.168.10.1 255.255.255.0
 ip access-group 101 out
end

PC1> ping 192.168.20.10
Request timed out.""",
        expected_fault=(
            "ACL 101 is applied outbound on Gi0/0, so it filters traffic heading toward the LAN "
            "rather than traffic sourced from it. It should be applied inbound."
        ),
    ),
    dict(
        case_id="NS-023",
        concept_tag="ACL",
        osi_layer=4,
        severity="High",
        symptom=(
            "Host 192.168.10.20 is supposed to be explicitly permitted to reach the server, but "
            "it is still blocked."
        ),
        topology_note="R1 filters inbound on Gi0/0 from the 192.168.10.0/24 LAN.",
        show_outputs="""R1#show access-lists
Extended IP access list 110
    10 deny ip 192.168.10.0 0.0.0.255 host 192.168.20.10 (417 matches)
    20 permit tcp host 192.168.10.20 host 192.168.20.10 eq www (0 matches)
    30 permit ip any any (52 matches)

R1#show running-config interface GigabitEthernet0/0
interface GigabitEthernet0/0
 ip access-group 110 in
end""",
        expected_fault=(
            "ACL entry ordering is wrong. The broad deny at sequence 10 matches first, so the "
            "specific permit at sequence 20 is never evaluated."
        ),
    ),
    dict(
        case_id="NS-024",
        concept_tag="ACL",
        osi_layer=4,
        severity="High",
        symptom=(
            "After applying a new ACL, web browsing works but DHCP renewals and all name "
            "resolution stopped working."
        ),
        topology_note="R1 filters inbound on Gi0/0. DNS + DHCP server = 192.168.10.5.",
        show_outputs="""R1#show access-lists
Extended IP access list 120
    10 permit tcp any any eq www (2043 matches)
    20 permit tcp any any eq 443 (1877 matches)

R1#show running-config interface GigabitEthernet0/0
interface GigabitEthernet0/0
 ip access-group 120 in
end

PC1> nslookup www.lab.local
DNS request timed out.

PC1> ipconfig /renew
DHCP request failed.""",
        expected_fault=(
            "ACL 120 only permits TCP 80 and 443. The implicit 'deny ip any any' at the end drops "
            "UDP 53 (DNS) and UDP 67/68 (DHCP)."
        ),
    ),
    dict(
        case_id="NS-025",
        concept_tag="ACL",
        osi_layer=4,
        severity="Critical",
        symptom=(
            "Guest VLAN clients can still open shares on the internal file server, even though a "
            "guest filter ACL was written and applied."
        ),
        topology_note="Guest VLAN 50 = 192.168.50.0/24 on Gi0/0.50. Internal VLAN 10 on Gi0/0.10.",
        show_outputs="""R1#show access-lists
Extended IP access list GUEST_FILTER
    10 deny ip 192.168.50.0 0.0.0.255 192.168.10.0 0.0.0.255 (0 matches)
    20 permit ip any any (0 matches)

R1#show running-config | include ip access-group|interface Gi
interface GigabitEthernet0/0.10
 ip access-group GUEST_FILTER in
interface GigabitEthernet0/0.50

GUEST-PC> ping 192.168.10.50
Reply from 192.168.10.50: bytes=32 time=2ms TTL=127""",
        expected_fault=(
            "GUEST_FILTER is applied inbound on Gi0/0.10 (the internal VLAN) instead of Gi0/0.50 "
            "(the guest VLAN), so guest-sourced traffic is never evaluated. Zero matches confirm it."
        ),
    ),

    # ----------------------------------------------------------------- NAT (3)
    dict(
        case_id="NS-026",
        concept_tag="NAT",
        osi_layer=3,
        severity="High",
        symptom=(
            "Internal hosts cannot reach the internet. NAT is configured on the router and the "
            "ISP link is up."
        ),
        topology_note="R1 Gi0/0 = LAN 192.168.10.1/24, Gi0/1 = 203.0.113.2/30 to ISP.",
        show_outputs="""R1#show ip nat translations

R1#show running-config | include ip nat|access-list 1
ip nat inside source list 1 interface GigabitEthernet0/1 overload
access-list 1 permit 192.168.10.0 0.0.0.255

R1#show running-config | section interface
interface GigabitEthernet0/0
 ip address 192.168.10.1 255.255.255.0
interface GigabitEthernet0/1
 ip address 203.0.113.2 255.255.255.252

R1#show ip nat statistics
Total active translations: 0 (0 static, 0 dynamic; 0 extended)
Outside interfaces:
Inside interfaces:
Hits: 0  Misses: 0""",
        expected_fault=(
            "Neither interface is marked with 'ip nat inside' or 'ip nat outside', so the NAT rule "
            "never engages. Note the empty interface lists in show ip nat statistics."
        ),
    ),
    dict(
        case_id="NS-027",
        concept_tag="NAT",
        osi_layer=3,
        severity="High",
        symptom=(
            "Hosts in VLAN 10 can browse the internet, but hosts in VLAN 20 cannot reach "
            "anything outside."
        ),
        topology_note="R1 NATs for both LANs. VLAN 10 = 192.168.10.0/24, VLAN 20 = 192.168.20.0/24.",
        show_outputs="""R1#show running-config | include ip nat|access-list
ip nat inside source list 1 interface GigabitEthernet0/1 overload
access-list 1 permit 192.168.10.0 0.0.0.255

R1#show running-config | section interface
interface GigabitEthernet0/0.10
 ip address 192.168.10.1 255.255.255.0
 ip nat inside
interface GigabitEthernet0/0.20
 ip address 192.168.20.1 255.255.255.0
 ip nat inside
interface GigabitEthernet0/1
 ip address 203.0.113.2 255.255.255.252
 ip nat outside

R1#show ip nat translations
Pro Inside global      Inside local       Outside local      Outside global
tcp 203.0.113.2:1044   192.168.10.25:1044 8.8.8.8:53         8.8.8.8:53""",
        expected_fault=(
            "The NAT source ACL only permits 192.168.10.0/24. VLAN 20 traffic is not matched, so "
            "it leaves untranslated and is dropped upstream."
        ),
    ),
    dict(
        case_id="NS-028",
        concept_tag="NAT",
        osi_layer=3,
        severity="Medium",
        symptom=(
            "Only one internal host can use the internet at a time. When a second host starts "
            "browsing, the first one loses connectivity."
        ),
        topology_note="R1 Gi0/1 = 203.0.113.2/30 is the only public address available.",
        show_outputs="""R1#show running-config | include ip nat
ip nat inside source list 1 interface GigabitEthernet0/1

R1#show ip nat translations
Pro Inside global      Inside local       Outside local      Outside global
--- 203.0.113.2        192.168.10.25      ---                ---

R1#show ip nat statistics
Total active translations: 1 (0 static, 1 dynamic; 0 extended)
Inside interfaces: GigabitEthernet0/0
Outside interfaces: GigabitEthernet0/1
Hits: 842  Misses: 219""",
        expected_fault=(
            "The 'overload' keyword is missing from the NAT statement, so the router does one-to-one "
            "NAT instead of PAT. With a single public IP only one host can translate at a time."
        ),
    ),

    # ------------------------------------------------------------ Wireless (3)
    dict(
        case_id="NS-029",
        concept_tag="Wireless",
        osi_layer=3,
        severity="Critical",
        symptom=(
            "A visitor connected to the Guest Wi-Fi was able to browse the internal file server "
            "and open staff shares."
        ),
        topology_note="Guest SSID should map to VLAN 50 (192.168.50.0/24). Internal VLAN 10 = 192.168.10.0/24.",
        show_outputs="""WLC/AP  Wireless > WLANs
  SSID: LAB-GUEST
    Interface/VLAN mapping : VLAN 10
    Security               : WPA2-PSK
    Client isolation       : Disabled

GUEST-LAPTOP> ipconfig
   IP Address. . . . . . . . . . . . : 192.168.10.87
   Default Gateway . . . . . . . . . : 192.168.10.1

GUEST-LAPTOP> ping 192.168.10.50
Reply from 192.168.10.50: bytes=32 time=3ms TTL=128

R1#show access-lists
Extended IP access list GUEST_FILTER
    10 deny ip 192.168.50.0 0.0.0.255 192.168.10.0 0.0.0.255 (0 matches)""",
        expected_fault=(
            "The guest SSID is mapped to internal VLAN 10 instead of guest VLAN 50. Guests land "
            "directly on the internal subnet, bypassing GUEST_FILTER entirely."
        ),
    ),
    dict(
        case_id="NS-030",
        concept_tag="Wireless",
        osi_layer=2,
        severity="Medium",
        symptom=(
            "A laptop will not associate with the lab SSID. It repeatedly prompts for the "
            "network password even though the passphrase is being typed correctly."
        ),
        topology_note="AP broadcasts SSID LAB-WIFI. Laptop is a Packet Tracer wireless end device.",
        show_outputs="""AP1  Config > Wireless
  SSID           : LAB-WIFI
  Authentication : WPA2-PSK
  Encryption     : AES
  Passphrase     : Cisco12345

LAPTOP1  Config > Wireless0
  SSID           : LAB-WIFI
  Authentication : WEP
  WEP Key        : 0123456789

AP1#show logging | include auth
%DOT11-4-AUTH_FAILED: Station 0060.4706.1A2B Authentication failed
%DOT11-4-AUTH_FAILED: Station 0060.4706.1A2B Authentication failed""",
        expected_fault=(
            "Security mode mismatch: the AP runs WPA2-PSK with AES while the client is set to WEP. "
            "The authentication method must match on both sides."
        ),
    ),
    dict(
        case_id="NS-031",
        concept_tag="Wireless",
        osi_layer=3,
        severity="High",
        symptom=(
            "Wireless clients associate with the AP successfully, signal is strong, but every "
            "client ends up with a 169.254.x.x address and no connectivity."
        ),
        topology_note="New SSID LAB-IOT was mapped to VLAN 60. Router uses subinterfaces per VLAN.",
        show_outputs="""AP2  Wireless > WLANs
  SSID: LAB-IOT
    Interface/VLAN mapping : VLAN 60
    Security               : WPA2-PSK
    Status                 : Enabled

IOT-CLIENT> ipconfig
   IP Address. . . . . . . . . . . . : 169.254.201.44
   Subnet Mask . . . . . . . . . . . : 255.255.0.0

AP2#show dot11 associations
  MAC Address    IP address      Device   State
  0060.2f11.99cd 169.254.201.44  client   Assoc

R1#show ip interface brief | include GigabitEthernet0/0
GigabitEthernet0/0.10      192.168.10.1    YES manual up    up
GigabitEthernet0/0.20      192.168.20.1    YES manual up    up
GigabitEthernet0/0.50      192.168.50.1    YES manual up    up

R1#show running-config | section dhcp
ip dhcp pool VLAN10_POOL
 network 192.168.10.0 255.255.255.0
ip dhcp pool VLAN50_POOL
 network 192.168.50.0 255.255.255.0""",
        expected_fault=(
            "VLAN 60 has no Layer 3 gateway subinterface and no DHCP pool. Association succeeds at "
            "Layer 2, but there is nothing to serve addresses, so clients fall back to APIPA."
        ),
    ),

    # ------------------------------------------------------------ Physical (2)
    dict(
        case_id="NS-032",
        concept_tag="Physical",
        osi_layer=1,
        severity="High",
        symptom=(
            "The link between R1 and R2 shows no activity. Cabling was checked and reseated but "
            "the interface stays down."
        ),
        topology_note="R1 Gi0/1 <-> R2 Gi0/1 serial-style WAN handoff, 10.0.0.0/30.",
        show_outputs="""R1#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0     192.168.10.1    YES manual up                    up
GigabitEthernet0/1     10.0.0.1        YES manual administratively down down

R1#show interfaces GigabitEthernet0/1
GigabitEthernet0/1 is administratively down, line protocol is down (disabled)
  Hardware is CN Gigabit Ethernet, address is 0090.2bb3.4401

R2#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/1     10.0.0.2        YES manual up                    down""",
        expected_fault=(
            "R1 Gi0/1 was left in a shutdown state. 'administratively down' means the interface "
            "was never brought up with 'no shutdown'."
        ),
    ),
    dict(
        case_id="NS-033",
        concept_tag="Physical",
        osi_layer=1,
        severity="Medium",
        symptom=(
            "File transfers between two buildings are extremely slow and occasionally drop, but "
            "ping succeeds and the link shows up/up."
        ),
        topology_note="SW1 Gi0/2 <-> SW2 Gi0/2 uplink between buildings.",
        show_outputs="""SW1#show interfaces GigabitEthernet0/2
GigabitEthernet0/2 is up, line protocol is up
  Full-duplex, 100Mb/s, media type is 10/100/1000BaseTX
     1247 input errors, 0 CRC, 0 frame, 0 overrun
     0 late collision, 0 deferred

SW2#show interfaces GigabitEthernet0/2
GigabitEthernet0/2 is up, line protocol is up
  Half-duplex, 100Mb/s, media type is 10/100/1000BaseTX
     0 input errors, 892 CRC, 0 frame, 0 overrun
     2043 late collision, 118 deferred

SW1#ping 10.0.99.2
Success rate is 100 percent (5/5)""",
        expected_fault=(
            "Duplex mismatch on the uplink: SW1 Gi0/2 is full-duplex while SW2 Gi0/2 is half-duplex. "
            "The late collision and CRC counters confirm it."
        ),
    ),
]

FIELDS = [
    "case_id",
    "symptom",
    "topology_note",
    "show_outputs",
    "expected_fault",
    "osi_layer",
    "concept_tag",
    "severity",
]


def main() -> None:
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases.csv")

    seen = set()
    for case in CASES:
        if case["case_id"] in seen:
            raise ValueError(f"duplicate case_id: {case['case_id']}")
        seen.add(case["case_id"])
        missing = set(FIELDS) - set(case)
        if missing:
            raise ValueError(f"{case['case_id']} missing fields: {sorted(missing)}")

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for case in CASES:
            writer.writerow({k: case[k] for k in FIELDS})

    tags = {}
    for case in CASES:
        tags[case["concept_tag"]] = tags.get(case["concept_tag"], 0) + 1

    print(f"wrote {len(CASES)} cases -> {out_path}")
    print("coverage: " + " | ".join(f"{k} {v}" for k, v in sorted(tags.items())))


if __name__ == "__main__":
    main()
