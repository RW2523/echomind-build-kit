"""Generate a realistic facility corpus into db/corpus/generated/.

    python -m scripts.generate_corpus            # write the corpus
    python -m scripts.generate_corpus --check    # verify invariants only

Why this exists: with only the eleven hand-written documents, retrieval returns k=8 out
of 12 chunks — two thirds of everything — so context precision is pinned at 1.000 and
measures nothing. A few hundred documents make the metric discriminate, make the
reranker worth switching on, and make a near-miss actually possible.

Two invariants the generator must never break, both asserted by --check:

  1. Nothing here may contradict the eleven authored documents. Every fact the golden
     set depends on (the 30-minute warm-up, 24-month biosafety validity, the 24-hour
     cancellation window, BC barcodes, 30/90-day retention) is stated in exactly one
     place, and generated documents refer to those documents rather than restating them.
  2. Nothing here may accidentally answer a redirect question. The golden set proves the
     system declines on parking permits, seminar rooms and equipment grants; a generated
     document that mentions them would turn a passing refusal into a failing one.

Deterministic: seeded RNG, so the corpus is identical on every run and the eval numbers
are comparable across runs.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

from server.config import REPO_ROOT

OUT = REPO_ROOT / "db" / "corpus" / "generated"
RNG_SEED = 20260809

# Topics the golden set expects the assistant to REFUSE. No generated document may
# mention them, or a correct refusal becomes a wrong answer.
FORBIDDEN_TOPICS = ["parking", "permit", "seminar room", "equipment grant", "grant application"]

# Facts owned by the authored corpus. Generated documents must not restate them with a
# different value; --check enforces that no contradicting number appears.
PROTECTED_FACTS = {
    "confocal warm-up": (r"warm[- ]?up", ["30 minute", "30-minute", "30 minutes"]),
    "biosafety validity": (r"Biosafety Level 2 .{0,40}valid", ["24 month"]),
    "cancellation window": (r"cancel\w* .{0,40}(within|before)", ["24 hour", "24-hour"]),
}

INSTRUMENTS = [
    ("Confocal C2", "Advanced Imaging Core", "point-scanning confocal", 42.00),
    ("Confocal C3", "Advanced Imaging Core", "point-scanning confocal", 46.00),
    ("Spinning Disk SD1", "Advanced Imaging Core", "spinning disk confocal", 55.00),
    ("Light Sheet LS7", "Advanced Imaging Core", "light sheet microscope", 68.00),
    ("Cryo-EM Titan", "Advanced Imaging Core", "cryo-electron microscope", 145.00),
    ("NovaSeq X", "Genomics Core", "high-output sequencer", 120.00),
    ("MiSeq M3", "Genomics Core", "benchtop sequencer", 38.00),
    ("Nanopore PromethION", "Genomics Core", "long-read sequencer", 74.00),
    ("Bioanalyzer B4", "Genomics Core", "capillary electrophoresis QC system", 22.00),
    ("Orbitrap Exploris", "Mass Spectrometry Core", "orbitrap mass spectrometer", 96.00),
    ("Q-TOF 6546", "Mass Spectrometry Core", "quadrupole time-of-flight system", 61.00),
    ("MALDI-TOF R2", "Mass Spectrometry Core", "MALDI time-of-flight system", 44.00),
]

LABS = [
    ("lab-a", "Patel Lab", "cell biology"),
    ("lab-b", "Ferreira Lab", "microbiology"),
    ("lab-c", "Okonkwo Lab", "structural biology"),
    ("lab-d", "Haruki Lab", "neuroscience"),
    ("lab-e", "Novak Lab", "immunology"),
    ("lab-f", "Silva Lab", "plant sciences"),
]

CONSUMABLES = [
    ("Type F immersion oil", "imaging", "Refractive index 1.518 at 23 °C."),
    ("Grade 1.2/1.3 holey carbon grids", "cryo", "Glow-discharge within 30 minutes of use."),
    ("Low-bind 1.5 mL tubes", "general", "Required for anything below 10 ng/µL."),
    ("Sequencing flow cell adapters", "genomics", "Single use; do not re-load."),
    ("LC-MS grade acetonitrile", "mass spec", "Open bottles expire after 60 days."),
    ("Calibration bead standard", "imaging", "Re-image the standard weekly."),
]


def _f(text: str) -> str:
    """Normalise the whitespace of a triple-quoted block."""
    return re.sub(r"\n{3,}", "\n\n", text.strip()) + "\n"


def instrument_sop(name: str, facility: str, kind: str, rate: float, rng) -> tuple[str, str]:
    startup = rng.choice([8, 10, 12, 15, 20])
    checks = rng.sample(
        ["stage levelling", "objective inspection", "filter turret alignment",
         "detector gain reset", "coolant reservoir level", "vacuum reading",
         "reference standard image", "buffer line purge"], 3)
    body = f"""
# {name} — Operating Procedure

{name} is a {kind} in the {facility}, charged at ${rate:.2f} per hour at the internal
rate. This procedure covers routine operation. Instrument-specific hazards and the
facility-wide start-up ordering rules are set out in the Core Facility General Policies
and, for the Confocal C2, in its own SOP — this document does not restate them.

## Before the session

Allow {startup} minutes for the enclosure to reach thermal equilibrium after opening.
Confirm the booking is active under your own account: sessions started against another
user's booking misattribute the charge.

Pre-session checks, in order:

1. {checks[0].capitalize()}.
2. {checks[1].capitalize()}.
3. {checks[2].capitalize()}.

## During the session

Record the acquisition parameters in the session log as you go. If the instrument
reports a fault code, stop and log it rather than power-cycling — repeated power cycling
is the most common cause of a corrupted calibration on this class of instrument.

Data written during the session lives on the instrument PC only until the retention
window in the Data Management and Retention policy expires. Copy it off before you leave.

## After the session

Return the stage to its home position, clean any immersion surfaces with lens tissue and
the supplied solvent, and complete the log sheet. Leave the enclosure closed.

## Escalation

A fault that stops the session goes to facility staff through the request system. Anything
smoking, leaking or alarming is a phone call to the duty officer, and the instrument comes
out of service immediately.
"""
    return f"{name} Operating Procedure", _f(body)


def instrument_maintenance(name: str, facility: str, kind: str, rate: float, rng) -> tuple[str, str]:
    interval = rng.choice([3, 6, 12])
    downtime = rng.choice([2, 4, 8, 24])
    body = f"""
# {name} — Preventive Maintenance Schedule

Maintenance on the {name} ({facility}) is planned on a {interval}-month cycle. Planned
work is blocked out in the calendar at least two weeks ahead, so it never collides with an
existing booking.

## Cycle

| Task | Interval | Typical downtime |
|---|---|---:|
| Full service | {interval} months | {downtime} h |
| Calibration check | monthly | 1 h |
| Consumable replacement | as required | 0.5 h |

## What users see

The instrument moves to `maintenance` status for the duration and cannot be booked. Users
with bookings inside an unplanned outage are cancelled at no charge and notified; this is
the facility's standing rule and is not specific to this instrument.

## Records

Every service, repair and alert is recorded against the instrument with its downtime, and
that record is what the monthly downtime reporting is built from. Ask the facility admin
for the maintenance history of a specific instrument — it is not visible to users directly.

## Known wear items

The {kind} in this configuration wears its consumable optical and fluidic parts fastest.
Budget for replacement at roughly {rng.choice([12, 18, 24])}-month intervals under normal
load; heavier use shortens that proportionally.
"""
    return f"{name} Preventive Maintenance Schedule", _f(body)


def instrument_troubleshooting(name: str, facility: str, kind: str, rate: float, rng) -> tuple[str, str]:
    symptoms = rng.sample(
        [("no signal at the detector", "check the shutter interlock and the filter path"),
         ("drifting focus over a long acquisition", "let the enclosure equilibrate longer"),
         ("unexpected noise in the baseline", "check grounding and the vibration isolation"),
         ("run aborts partway through", "check consumable seating and free disk space"),
         ("intensity varying between fields", "re-image the reference standard"),
         ("sample heating during acquisition", "reduce source power and increase averaging")], 4)
    rows = "\n".join(f"| {s} | {fix.capitalize()}. |" for s, fix in symptoms)
    body = f"""
# {name} — Troubleshooting Guide

First-line troubleshooting for the {name} in the {facility}. Work through this before
raising a fault: roughly half of reported faults on this instrument resolve here.

## Common symptoms

| Symptom | First thing to try |
|---|---|
{rows}

## When to stop

Stop and raise a request if the instrument reports a hardware fault code, if a fix would
require opening an enclosure panel, or if the same symptom recurs within one session after
a successful fix. Do not attempt any adjustment that requires tools.

## What to include in the report

The instrument name, the time, the fault code if there is one, and what you had just
changed. A report without the acquisition parameters usually costs a day while staff
reproduce it.
"""
    return f"{name} Troubleshooting Guide", _f(body)


def instrument_booking_notes(name: str, facility: str, kind: str, rate: float, rng) -> tuple[str, str]:
    typical = rng.choice([1, 2, 3, 4])
    body = f"""
# Booking Notes — {name}

Practical guidance for scheduling the {name} ({facility}). The cancellation window, the
fair-share caps and the maximum session length are facility-wide and set out in the
Booking and Cancellation Rules; this note covers only what is particular to this
instrument.

## Typical session length

Most users book {typical}–{typical + 2} hours. Sessions shorter than an hour rarely pay
for the set-up time on a {kind}.

## Demand

Demand concentrates in the middle of the week. If your work is not time-critical, the
first and last days of the week are consistently easier to book at short notice.

## Account codes

The booking is charged to the account code on the booking, not to the person. Check the
code before confirming, particularly if you hold codes for more than one lab.

## Training

Booking is blocked outright without current training on this instrument. The scheduling
system refuses rather than warns, so an unexpected refusal is usually a lapsed
certification — the validity periods are in the training requirements document.
"""
    return f"Booking Notes — {name}", _f(body)


def lab_protocol(lab_id: str, lab_name: str, field_name: str, n: int, rng) -> tuple[str, str, str]:
    topic = rng.choice(["sample fixation", "buffer preparation", "cryopreservation",
                        "extraction", "quality control", "staining", "digestion"])
    temp = rng.choice([4, 20, 25, 37])
    minutes = rng.choice([5, 10, 15, 30, 45, 60])
    body = f"""
# {lab_name} — {topic.title()} Protocol {n}

Internal to the {lab_name} ({field_name}). House protocol; not facility guidance, and not
to be circulated outside the lab.

## Reagents

Prepare fresh on the day. Anything held longer than 24 hours at {temp} °C is discarded
rather than used — the failure mode is silent and shows up as inconsistent results a week
later.

## Method

1. Equilibrate samples to {temp} °C for {minutes} minutes.
2. Apply the working solution at the dilution recorded in the lab's reagent register.
3. Incubate for {minutes} minutes with gentle agitation.
4. Wash three times; do not let the sample dry between washes.

## Notes

Our dilutions differ deliberately from the vendor sheets. Where they differ, the register
is authoritative and the vendor sheet is not.

Instrument settings for downstream acquisition are recorded per project, not here. Ask the
PI before changing any of them.
"""
    return f"{lab_name} {topic.title()} Protocol {n}", _f(body), lab_id


def facility_note(kind: str, n: int, rng) -> tuple[str, str]:
    if kind == "safety":
        hazard = rng.choice(["cryogens", "class 3B lasers", "biological material",
                             "compressed gases", "solvents", "UV sources"])
        body = f"""
# Safety Notice {n} — Working with {hazard.title()}

Applies wherever {hazard} are in use in the cores.

## Before you start

Confirm your training covers this hazard class. Training validity periods are in the
training requirements document and are enforced by the booking system.

## Controls

Use the engineering controls first: interlocks, extraction, and shielding. Personal
protective equipment is the last line, not the first. Never defeat an interlock, including
"briefly" during alignment.

## If something goes wrong

Make the area safe, then report it the same day through the request system. An incident
report is not a disciplinary process; an unreported incident is.

## Review

This notice is reviewed annually by the facility safety officer.
"""
        return f"Safety Notice {n} — Working with {hazard.title()}", _f(body)

    if kind == "training":
        module = rng.choice(["Instrument Induction", "Data Handling", "Sample Handling",
                             "Image Analysis", "Statistics for Core Users",
                             "Reagent Management"])
        hours = rng.choice([2, 3, 4, 8])
        body = f"""
# Training Module {n} — {module}

A {hours}-hour module offered by the cores. This module is additional to instrument
training and does not by itself grant booking rights on any instrument.

## Who it is for

Users who have completed onboarding and hold at least one instrument certification.
New users should complete onboarding first — the onboarding guide describes that process.

## Content

- What the cores record, and where it lives.
- Reading the records for your own work.
- Common mistakes and how they show up months later.
- A worked example using the facility's own reporting.

## Assessment

A short practical exercise. There is no pass mark; the exercise exists so the trainer can
see what did not land.

## Scheduling

Runs when there is demand for a cohort of four or more. Register through the request
system.
"""
        return f"Training Module {n} — {module}", _f(body)

    if kind == "faq":
        subject = rng.choice(["Sample Logistics", "Scheduling", "Data Transfer",
                              "Reagents and Consumables", "Working Across Cores",
                              "Reporting Problems"])
        body = f"""
# FAQ {n} — {subject}

Questions the cores are asked most often about {subject.lower()}.

**Who do I ask first?**
Facility staff for anything about instruments, access or charges. Your PI for anything
about experimental design — the cores advise on what an instrument can do, not on what
your experiment should be.

**How quickly will I get an answer?**
Requests raised through the system are triaged the next working day. Anything blocking an
instrument is looked at the same day.

**Can I change a submitted request?**
Yes, until it moves out of `submitted`. After that, raise a new request and reference the
old one rather than editing in place, so the history stays intact.

**Where do I find what I was charged?**
Your invoice lines, per account code and month. The billing FAQ explains how charges are
formed and how to dispute one.

**What if two cores are involved?**
Raise a request in each; they are tracked separately. Mention the other request so staff
can sequence the work.
"""
        return f"FAQ {n} — {subject}", _f(body)

    consumable, area, detail = CONSUMABLES[n % len(CONSUMABLES)]
    body = f"""
# Consumable Standard {n} — {consumable}

The {area} cores stock {consumable} centrally. Users should not substitute their own
without checking with staff first.

## Specification

{detail} Substituting a different specification is the usual cause of results that cannot
be compared across sessions.

## Ordering

Stock is held for routine use. For anything beyond routine quantities, raise a request at
least two weeks ahead — some items are made to order.

## Storage

Store as marked on the container. Items with an opened-date field must have it filled in;
an undated open container is discarded at the next audit.

## Charging

Consumables appear as their own lines on the monthly invoice, separate from instrument
time.
"""
    return f"Consumable Standard {n} — {consumable}", _f(body)


def build(rng: random.Random) -> list[tuple[str, str, str, str | None]]:
    """Return (filename, title, body, lab_id | None)."""
    docs: list[tuple[str, str, str, str | None]] = []

    seen: set[str] = set()

    def slug(t: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
        # Distinct titles can slug to the same filename; without this the later document
        # silently overwrites the earlier one on disk.
        name, n = base, 2
        while name in seen:
            name, n = f"{base}-{n}", n + 1
        seen.add(name)
        return name

    for name, facility, kind, rate in INSTRUMENTS:
        for maker in (instrument_sop, instrument_maintenance,
                      instrument_troubleshooting, instrument_booking_notes):
            title, body = maker(name, facility, kind, rate, rng)
            docs.append((f"{slug(title)}.md", title, body, None))

    for lab_id, lab_name, field_name in LABS:
        for n in range(1, 6):
            title, body, lab = lab_protocol(lab_id, lab_name, field_name, n, rng)
            docs.append((f"{slug(title)}.md", title, body, lab))

    counters = {"safety": 0, "training": 0, "faq": 0, "consumable": 0}
    plan = ["safety"] * 14 + ["training"] * 12 + ["faq"] * 12 + ["consumable"] * 6
    for kind in plan:
        counters[kind] += 1
        title, body = facility_note(kind, counters[kind], rng)
        docs.append((f"{slug(title)}.md", title, body, None))

    return docs


def check(docs: list[tuple[str, str, str, str | None]]) -> list[str]:
    problems = []
    for filename, title, body, _lab in docs:
        low = body.lower()
        for topic in FORBIDDEN_TOPICS:
            if topic in low:
                problems.append(f"{filename}: mentions redirect topic {topic!r}")
        for fact, (pattern, allowed) in PROTECTED_FACTS.items():
            if re.search(pattern, low) and not any(a in low for a in allowed):
                problems.append(f"{filename}: restates protected fact {fact!r}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    rng = random.Random(RNG_SEED)
    docs = build(rng)

    problems = check(docs)
    if problems:
        print(f"{len(problems)} invariant violation(s):")
        for p in problems[:20]:
            print(f"  {p}")
        return 1
    print(f"invariants OK across {len(docs)} documents")
    if args.check:
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.md"):
        stale.unlink()

    by_visibility = {"public": 0, "lab": 0}
    for filename, title, body, lab in docs:
        visibility = "lab" if lab else "public"
        by_visibility[visibility] += 1
        front = [f"---", f'title: "{title}"', 'version: "1.0"',
                 f"visibility: {visibility}"]
        if lab:
            front.append(f"lab: {lab}")
        front.append("---")
        (OUT / filename).write_text("\n".join(front) + "\n\n" + body, encoding="utf-8")

    print(f"wrote {len(docs)} documents to {OUT.relative_to(REPO_ROOT)}")
    print(f"  public {by_visibility['public']}, lab-scoped {by_visibility['lab']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
