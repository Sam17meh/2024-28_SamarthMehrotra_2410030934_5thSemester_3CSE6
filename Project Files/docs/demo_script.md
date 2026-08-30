# Demo Video Script — NetSage AI

**Target length:** 7 minutes (the brief asks for 5–10). Timings below total 6:50, leaving
room to breathe.

**Before you start recording**

```bash
cd "C:/Users/samar/Desktop/Cisco/NetSage-AI"
python src/netsage.py dashboard
```

Then set up:
- Terminal, maximised, font size up to at least 16pt so it's readable in a compressed video
- `outputs/dashboard.html` open in a browser tab
- `docs/responsible_ai_log.md` open in a second tab
- Close everything else. No notifications.

Record with OBS or the Xbox Game Bar (`Win+G`). Record audio in one take if you can —
narration cut over silent screen capture always sounds narrated.

**The one thing to get right:** this project's selling point is not that the AI is clever.
It is that the AI is *checked*. Every section below is building toward the 4:30 mark, where
the tool refuses to act on an AI suggestion. Don't rush to get there and don't undersell it.

---

## 0:00 – 0:35 · Who and what

**On screen:** slide 1 of the PPT, or just the terminal with the repo listed.

> "I'm Samarth Mehrotra, enrollment 2410030934, B.Tech CSE at IILM University Greater Noida.
> This is my Cisco Virtual Internship project — Problem Statement 2, Applied AI and Network
> Troubleshooting.
>
> The system is called NetSage AI. It takes a network fault report and the `show`-command
> output from the affected devices, and it produces a diagnosis: root cause, the OSI layer,
> the evidence it's based on, and the fix. What makes it worth building is the part that
> stops it: no fix it suggests can be applied until a human has reviewed and approved it.
> I'll show you that refusing to act, live, at the end."

---

## 0:35 – 1:25 · The case library

**On screen:** `data/cases.csv` in a viewer, then scroll one case's `show_outputs` field.

```bash
python -c "import csv,sys; csv.field_size_limit(10**7); r=list(csv.DictReader(open('data/cases.csv',encoding='utf-8'))); print(len(r),'cases'); print(sorted({x['concept_tag'] for x in r}))"
```

> "The dataset is 33 troubleshooting cases — the brief asks for 30. Nine fault families:
> VLAN, gateway, DHCP, DNS, routing, ACL, NAT, wireless and physical layer. They span OSI
> layers 1, 2, 3, 4 and 7.
>
> Each case carries a symptom as a user would report it, a topology note, and the actual CLI
> transcript — `show vlan brief`, `show ip route`, `show access-lists`, `ipconfig /all`,
> whatever the fault calls for. And an `expected_fault` column, which is the ground truth.
> That column is never shown to the AI. It exists so I can measure whether the AI was right,
> instead of assuming it."

---

## 1:25 – 2:30 · The deterministic layer

**On screen:** terminal.

```bash
python src/netsage.py check --case NS-021
```

> "Before any AI touches a case, it goes through a pure-Python rule checker. 34 checks, no
> model involved, no network calls. This is NS-021 — hosts in one subnet are unreachable
> above a certain address.
>
> The check that fires is `route_mask_suspicious`. It compares the prefix length of the
> static route against the connected prefix for the same network on the other router, sees
> /26 where /24 was intended, and quotes the exact transcript line it matched on."

Then:

```bash
python src/netsage.py check --all | tail -4
```

> "Across all 33 cases: 32 produce at least one finding. That's a 97% catch rate, and I want
> to be precise about what it measures — it means a check fired on a genuine fault, not that
> the check named the fault perfectly.
>
> One case is missed, deliberately. I'll come back to it."

---

## 2:30 – 3:30 · The AI layer

**On screen:** terminal.

```bash
python src/netsage.py diagnose --case NS-021
```

> "Now the AI layer on the same case. The prompt library is in `prompts/` — the main one is
> `diagnose_prompt.md`, and it requires structured JSON: root cause, OSI layer, confidence,
> evidence, next command to run, and fix steps.
>
> Two things are enforced in code, not just requested in the prompt. First, the output is
> validated against a schema — nine required fields, correct types, and it cannot set
> `requires_human_review` to false. Second, and this is the one I'm proud of: every evidence
> line is checked for verbatim containment in the case transcript."

**Point at the `evidence` line in the output.**

> "Here it passes. On NS-014 it doesn't —"

```bash
python src/netsage.py diagnose --case NS-014
```

> "— two of the three evidence lines are paraphrases. `'DNS request timed out. (timeout was
> 2 seconds)'` collapses two real lines into one and adds parentheses that appear nowhere in
> the transcript. The diagnosis is *correct*, but two of its citations are reconstructed.
>
> That's the dangerous kind of wrong, because it's recognisable enough to skim past. The
> validator catches it automatically. Across 33 cases it flags exactly one — so it's
> detecting a real problem, not just firing on everything."

---

## 3:30 – 4:30 · Human review

**On screen:** run the review view, but don't answer the prompt — hit Ctrl+C or `s` to skip.

```bash
python src/netsage.py review --case NS-031
```

> "This is the reviewer's view. The order matters: raw transcript first, then the
> deterministic findings, then the AI's conclusion last. If you read a confident AI summary
> first, you tend to read the evidence looking for confirmation of it.
>
> NS-031 is a wireless case. The AI says the WPA2 four-way handshake is failing and wants
> the pre-shared key re-entered. But look at the evidence it quoted itself: `State Assoc`.
> That means the handshake already completed. Its own citation rules out its own conclusion.
>
> The real fault: VLAN 60 has no Layer 3 subinterface and no DHCP pool. Clients associate
> fine and then have nothing to answer their DHCP request, so they fall back to APIPA. I
> rejected this one."

Skip the prompt, then:

```bash
python src/netsage.py review --summary
```

> "All 33 reviewed: 21 accepted, 7 edited, 5 rejected. A 63.6% agreement rate and 12
> corrections, where the brief asks for 5.
>
> I want to be straight about that number: I could have regenerated the wrong answers until
> the model agreed with me and reported 100%. That would have made this project worthless. A
> 100% agreement rate on an AI troubleshooting tool means either the test is rigged or
> nobody checked."

---

## 4:30 – 5:30 · The gate — **the centrepiece**

**On screen:** terminal. Slow down here. Let the output sit.

```bash
python src/netsage.py apply --case NS-031
```

> "So the AI proposed a fix for NS-031. Let's try to apply it."

**Pause. Let the `REFUSED` block render.**

> "It refuses. It names who rejected it, and it quotes the reason back.
>
> This isn't a warning I can click through. `netsage.py apply` calls `review.can_apply()`
> before it prints a single fix step, and there is no code path from a diagnosis to a fix
> that goes around it. No review, or a rejected review, means no output. If I'd applied this
> one, I'd have deauthenticated every client on that SSID and not fixed the actual problem."

```bash
python src/netsage.py apply --case NS-007
```

> "NS-007 was accepted on review, so the same command works — and where the reviewer edited
> the diagnosis, it prints the reviewer's corrected root cause above the AI's and says
> plainly which one supersedes.
>
> That's the difference between documenting human oversight and enforcing it."

---

## 5:30 – 6:20 · The dashboard

**On screen:** switch to the browser, `outputs/dashboard.html`. Scroll steadily.

> "Everything joins into a dashboard — pandas over the case library, the review log, and a
> live run of the rule checker. The catch rate is recomputed on every build, so it can't
> drift away from what the code actually does.
>
> Coverage by concept and by OSI layer. Severity. And this one is the whole architecture in
> one chart —"

**Stop on the "Where each fault was actually caught" chart.**

> "— 21 cases where the deterministic checker and the AI agreed. 7 where the checker caught
> it and the AI needed correcting. 4 where the checker caught it and the AI was simply
> wrong. And one where neither caught it.
>
> That last one is NS-002, the case the rule checker misses. A switch port is in VLAN 30
> when it should be in VLAN 20. Nothing in the configuration is malformed — it's internally
> consistent and only the *intent* is violated. No rule can know which VLAN a port is
> supposed to be in. The AI got it wrong too; it blamed the DHCP server, because
> `DHCP Server` was the most prominent line in the output.
>
> I left that case uncovered on purpose. It's the honest boundary of the system: the
> deterministic layer can't hallucinate but can't reason about intent, the AI can reason but
> can invent, and there are faults that need a person."

**Scroll to a table view.**

> "Every chart has a table underneath it, and the whole thing exports to
> `dashboard_summary.csv`, so nothing depends on being able to distinguish colours."

---

## 6:20 – 6:50 · Close

**On screen:** terminal.

```bash
python -m pytest tests/ -q
```

> "65 tests. Several are regression tests for bugs I found while building this — including
> one where a failed ping was being read as a success because the text `Reply from` appears
> in `Destination host unreachable`, which silently disabled every connectivity check
> downstream.
>
> All 12 corrections are documented in `docs/responsible_ai_log.md` with the failure mode,
> what the AI leaned on, and the change each one drove — two changed the prompt, the rest
> changed the checker or the review procedure. That distribution is a finding in itself:
> most of these weren't fixable by asking the model more nicely.
>
> Thank you."

---

## Recording notes

- **Do not** run `netsage.py all` on camera. It produces a wall of output.
- If a live command is slow, the recorded provider is the default and needs no network. The
  demo cannot fail from a connectivity problem.
- The `review --case` command is **interactive**. Answer `s` to skip, or Ctrl+C. Don't record
  yourself accidentally appending a duplicate decision to the log.
- Keep the terminal at 78 columns or wider — the report rules are 78 chars and wrap ugly
  below that.
- If you fumble a line, keep going and cut it later. Restarting takes longer than trimming.
- Say the enrollment number clearly at 0:00. Some reviewers check it against the form.
