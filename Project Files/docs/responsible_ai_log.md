# Responsible AI Log

**Project:** NetSage AI — Applied AI + Network Troubleshooting
**Reviewer:** Samarth Mehrotra, IILM University Greater Noida
**Corpus:** 33 cases · **Reviewed:** 33 · **Corrections:** 12
**Requirement:** at least 5 documented corrections. This log has 12.

---

## What this document is, and what it is not

This is a record of an AI system being caught getting things wrong, by a human, on
purpose-built cases where the right answer was known independently.

Two things need saying up front, because they determine how much the numbers below are
worth.

**The recorded diagnoses are genuine model output, not fabricated failures.** Each one was
produced by Claude Opus 5 reading a case's symptom, topology note and `show`-command
transcript against `prompts/diagnose_prompt.md`. The `expected_fault` column was never in
the prompt. They are committed verbatim in `outputs/ai_responses.json`, including the
twelve wrong ones, because deleting a wrong answer and regenerating until the model agreed
with me would have produced a 100% agreement rate and destroyed the only interesting
result in the project.

**This is a demonstration corpus, not a blind benchmark.** The recorded responses were
generated in the same working session that authored the case library. I did not have the
model grade itself, and no answer key was passed to it — but I also cannot claim the
independence a real benchmark needs. The honest description is: these are real model
failures on realistic inputs, collected to show what the review layer catches. Anyone
wanting a genuinely blind run can execute `--provider anthropic` against a fresh case set;
the code path exists and is used by nothing else.

**Agreement rate: 63.6%** (21 of 33 accepted with no change). That number is the point. A
troubleshooting assistant that a human agrees with two times in three is useful and
dangerous in exactly the proportions this log documents.

---

## Failure-mode taxonomy

The nine categories in `src/review.py`, and how the 12 corrections distribute:

| Failure mode | Count | What it means |
|---|---:|---|
| surface-signal bias | 2 | Named the most visible symptom instead of the underlying cause |
| plausible-but-wrong mechanism | 2 | Invented a mechanism the supplied evidence contradicts |
| missed second fault | 2 | Primary diagnosis right, an independent second problem unreported |
| platform-inappropriate command | 1 | Command not valid on the device in question |
| unsafe fix proposed | 1 | Cause right, fix causes collateral damage |
| evidence not verbatim | 1 | Paraphrased a cited line instead of quoting it |
| fix violates design intent | 1 | Technically resolves the fault, against the documented design |
| incorrect protocol reasoning | 1 | Got the protocol's own behaviour wrong |
| wrong device blamed | 1 | Right fault class, wrong end of the link |

No single mode dominates. That is itself a finding: there is no one prompt sentence that
would have prevented most of these.

---

## The five rejections

A rejection means the diagnosis was wrong, not merely imprecise. `netsage.py apply` refuses
to emit fix steps for any of these five.

### NS-002 — DHCP · surface-signal bias

**AI said:** the DHCP server is handing out addresses from the wrong scope, and proposed
reserving the host's MAC in the Sales pool.

**Evidence it leaned on:** the `DHCP Server . . . : 192.168.30.1` line from `ipconfig /all`
— the most prominent line in the output, and the one that names a server.

**Actually wrong:** `Fa0/5` is a member of VLAN 30 (GUEST) instead of VLAN 20 (SALES). The
host is in the Guest broadcast domain and is leasing from the Guest scope. DHCP is behaving
exactly as configured.

**Why the correction matters:** the proposed fix would have handed the host a
`192.168.20.x` address while it remained in VLAN 30 — an address with no reachable gateway,
strictly worse than the original fault. The AI's own evidence list did not include the VLAN
table at all, which is where the answer was.

**Also the one case the deterministic checker misses.** No rule fires on NS-002, because no
rule can know which VLAN a port *should* be in — the running config is internally
consistent and only the intent is violated. Both automated layers failed; the human caught
it. This is the honest limit of the architecture and it is documented rather than hidden.

**Change made:** none to the prompt. This is not a prompt-fixable failure; the model would
need the design intent, which is not in the transcript. Instead the review procedure
changed: `render_case()` now prints the deterministic findings *before* the AI's conclusion,
and prints "no deterministic check fired — scrutinise it harder" when the checker is
silent, so a silent checker is a warning rather than an absence.

### NS-012 — DHCP · surface-signal bias

**AI said:** the hosts have the wrong default gateway configured, and proposed editing each
PC by hand.

**Evidence it leaned on:** it quoted the pool's own `default-router 192.168.10.254` line —
so the cause was in its evidence list while it reported the effect.

**Actually wrong:** the DHCP pool's `default-router` statement is wrong. The router's LAN
address is `192.168.10.1`. Every client is misconfigured because the server is telling them
to be.

**Why the correction matters:** the proposed fix is self-defeating. Every host corrected by
hand reverts at its next lease renewal. One line on the router fixes all of them
permanently.

**Change made:** this failure and NS-031 drove **prompt v1.2**, which added **Rule 5 —
report the root cause, not the surface symptom**, with the explicit instruction that when a
setting is distributed by a server, the server's configuration is the cause and the clients
are the symptom.

### NS-021 — Routing · plausible-but-wrong mechanism

**AI said:** the route is present and correct; the failures are probably powered-off hosts
or host firewalls.

**Evidence it leaned on:** it quoted `S 192.168.20.0/26 [1/0] via 10.0.0.2` and then
concluded the route was fine.

**Actually wrong:** the static route was written with mask `255.255.255.192` (/26) instead
of /24. It covers `192.168.20.0`–`.63` only. Anything from `.64` up has no matching route on
R1. The fault is inside the line it cited.

**Why the correction matters:** worst outcome of any case in the corpus. The reviewer is
sent to check hardware and host firewalls while the router configuration stays broken. The
giveaway the model missed is that the reachability boundary falls exactly at `.63` — a host
firewall does not switch on at a subnet boundary. R2's connected `/24` was in the same
transcript, available for comparison, and was not compared.

**Change made:** prompt v1.2's Rule 6 (consider multiple faults) was extended with an
instruction to compare a route's prefix length against the connected prefix for the same
network wherever both appear. A deterministic check, `route_mask_suspicious`, was also
added — it now catches this case without the model's help, which is the more reliable fix.

### NS-025 — ACL · incorrect protocol reasoning

**AI said:** the ACE's source and destination are reversed, and proposed swapping them.

**Actually wrong:** the ACE reads `deny ip 192.168.50.0 0.0.0.255 192.168.10.0 0.0.0.255` —
source guest, destination internal — which is correct for blocking guest-to-internal.
`GUEST_FILTER` is applied inbound on `Gi0/0.10`, the internal subinterface, instead of
`Gi0/0.50` where guest traffic enters.

**Why the correction matters:** applying this fix would have created a second security hole
(blocking internal hosts from reaching guests) while leaving the actual violation wide open.
The model collapsed a real distinction: **0 matches means the ACL is never in the traffic
path, not that its contents are wrong.**

**Change made:** the `acl_zero_matches` check was written to state that distinction in its
message text, so the reviewer sees the correct interpretation of a zero-match counter
alongside the AI's incorrect one. No prompt change — the reasoning error is specific enough
that a general rule would not have caught it.

### NS-031 — Wireless · plausible-but-wrong mechanism

**AI said:** the WPA2 four-way handshake is failing, and proposed re-entering the
pre-shared key on the WLAN.

**Refuted by a line the diagnosis itself quoted:** `State Assoc` means the handshake already
completed. A failing key exchange is not merely unproven here, it is impossible.

**Actually wrong:** VLAN 60 has no Layer 3 subinterface on R1 and no DHCP pool. Association
succeeds at Layer 2, but nothing exists to answer a DHCPDISCOVER, so clients fall back to
APIPA. The transcript lists subinterfaces for VLANs 10, 20 and 50 and pools for 10 and 50;
60 appears in neither, and the model reproduced that output without comparing it.

**Why the correction matters:** the proposed fix would have deauthenticated every client on
the SSID for no benefit.

**Change made:** second driver of **prompt v1.2**. Rule 5 was written to cover this shape
too — a state indicator that proves a stage completed rules out every failure mode at or
before that stage. This is the case `docs/demo_script.md` uses for the apply-gate
demonstration.

---

## The seven edits

An edit means the diagnosis was broadly right and the reviewer corrected it. The corrected
version, not the AI's, is what `apply` presents as authoritative.

### NS-008 — Gateway · platform-inappropriate command

Root cause correct: no `ip default-gateway` on SW3. The fix proposed
`ip route 0.0.0.0 0.0.0.0`, which only takes effect on a switch with `ip routing` enabled —
this is a Layer 2 switch with a management SVI. Corrected to
`ip default-gateway 10.0.99.1`.

Notable: the AI quoted the `include default-gateway` line as its own evidence and then
proposed a different command. It identified the missing feature and reached for the router
idiom anyway. **Change made:** prompt Rule 4 (use real IOS commands) was extended to
require that the command match the device class, and the worked examples now include a
Layer 2 switch.

### NS-010 — DHCP · unsafe fix proposed

Diagnosis correct and cleanly evidenced: pool exhausted, 244 of 244 leased. The fix began
with `clear ip dhcp binding *`, which wipes every active lease in the lab — every working
machine loses its address to recover the few that never got one. Reordered to shorten the
lease timer first and let bindings recycle.

The AI's *secondary* finding — that a /24 is at its structural ceiling — was the actually
important point and belonged in the primary answer. **Change made:** none to the prompt; the
review checklist in `prompts/reviewer_assist_prompt.md` gained "fix safety" as an explicit
check, since a correct diagnosis with a destructive fix is the failure mode most likely to
be waved through.

### NS-011 — DHCP · missed second fault

Primary diagnosis correct: pool network is `192.168.99.0/24` while the serving interface is
`192.168.10.1/24`. It missed that `ip dhcp excluded-address 192.168.10.1 192.168.10.10` is
in the same output, currently excluding addresses from a subnet the pool does not serve.
Harmless today, load-bearing the moment the network statement is fixed. Added as a
secondary finding on edit.

### NS-014 — DNS · evidence not verbatim

Root cause correct: host points at DNS `192.168.10.53`; the service runs on
`192.168.10.5`. Two of three evidence lines were not quotations:

- `"DNS request timed out. (timeout was 2 seconds)"` — collapses two transcript lines into
  one and adds parentheses
- `"PC1> ping 192.168.10.53 -> Request timed out."` — invents an arrow that appears nowhere

Both are *recognisable*, which is what makes them dangerous: a reviewer skimming would
accept them. Confidence of `high` was also unearned when the supporting quotes were
reconstructed.

**Change made, and this is the most important one in the log.** This drove **prompt v1.1**
and **Rule 2 (evidence must be verbatim)** — but a prompt rule is a request, not a control.
So `schema.validate_evidence()` was written to enforce it in code: every evidence string is
whitespace-normalised and checked for containment in the case transcript, and any line that
fails is flagged before a human sees the diagnosis. Across all 33 cases it flags exactly
one — NS-014 — which is both the correct answer and a demonstration that the check is not
firing indiscriminately. Evidence integrity rate: **97.0%**, measured rather than claimed.

### NS-020 — Routing · fix violates design intent

Diagnosis and evidence correct: area mismatch on the shared link, area 0 on R1 and area 1
on R2. The fix moved R1 out of area 0, which would leave the topology with no backbone at
all — a non-backbone area cannot exist without area 0 to attach to. Reversed on edit: R2
moves into area 0, because the transit link belongs in the backbone.

The AI had no way to know which area was intended. **The honest move would have been to say
so.** **Change made:** the confidence-calibration section of the prompt now states that when
two configurations conflict and the transcript does not establish which is intended, the
correct output is `medium` confidence plus an explicit statement of the ambiguity — not a
silent choice.

### NS-024 — ACL · missed second fault

Root cause right and it named both DNS and DHCP as blocked: ACL 120 permits only TCP 80 and
443, and the implicit deny drops UDP 53 and UDP 67/68. The fix then only added a permit for
UDP domain, so DHCP renewals stayed broken — and the AI had quoted `DHCP request failed.` as
its own evidence. Added `permit udp any any eq bootps` plus the bootpc reply direction on
edit.

A reviewer who accepted this would have fixed half the reported fault and closed the
ticket. **Change made:** `reviewer_assist_prompt.md` check 5 now asks explicitly whether the
fix addresses *every* symptom named in the root cause.

### NS-033 — Physical · wrong device blamed

Fault class right and the counter evidence read correctly: duplex mismatch on the Gi0/2
uplink. Two problems. It named SW1 as "the misconfigured end" when nothing in the transcript
establishes which side was set by hand — late collisions on SW2 prove disagreement, not
authorship. And the fix set SW1 to half-duplex, resolving the mismatch by permanently
degrading a building uplink; the reported symptom was slow transfers, so this would have
closed the ticket without fixing the complaint. Corrected to bring SW2 up to full, or set
both to auto.

---

## Prompt version history

| Version | Driven by | Change |
|---|---|---|
| v1.0 | — | Initial: structured JSON, six hard rules, three worked examples |
| v1.1 | NS-014, NS-031 | Rule 2 tightened — evidence must be a verbatim copy, not a recognisable paraphrase. Confidence calibration added. |
| v1.2 | NS-012, NS-031 | Rule 5 added — report the root cause, not the surface symptom. Server-distributed settings, and state indicators that rule out earlier failure stages. |

Two of the twelve corrections drove prompt changes. The rest drove changes to the
deterministic checker, the reviewer's checklist, or the order in which information is shown
to the reviewer. That distribution is worth stating plainly: **most of these failures were
not fixable by asking the model more nicely.**

---

## What the human oversight is, mechanically

Not a policy. A control, in three parts:

1. **`schema.validate_evidence()`** runs before any human sees a diagnosis. Evidence that
   cannot be located in the transcript is flagged in the review view with
   `<-- NOT FOUND IN TRANSCRIPT`, and a `!!!` warning block is printed.
2. **`review.render_case()`** shows the raw transcript before the AI's interpretation, and
   the deterministic findings before the AI's conclusion. A reviewer who reads a confident
   summary first tends to read the evidence looking for confirmation of it.
3. **`review.can_apply()`** is consulted by `netsage.py apply` before a single fix step is
   printed. No review, or a Rejected review, means no fix output — and there is no code path
   from a diagnosis to a fix that bypasses it.

Verify point 3 directly:

```bash
python src/netsage.py apply --case NS-031
```

It refuses, names the reviewer, and quotes the rejection note. `NS-007` on the same command
succeeds. That difference is the deliverable.
