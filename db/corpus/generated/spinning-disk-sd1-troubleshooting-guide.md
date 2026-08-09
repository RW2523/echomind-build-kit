---
title: "Spinning Disk SD1 Troubleshooting Guide"
version: "1.0"
visibility: public
---

# Spinning Disk SD1 — Troubleshooting Guide

First-line troubleshooting for the Spinning Disk SD1 in the Advanced Imaging Core. Work through this before
raising a fault: roughly half of reported faults on this instrument resolve here.

## Common symptoms

| Symptom | First thing to try |
|---|---|
| run aborts partway through | Check consumable seating and free disk space. |
| intensity varying between fields | Re-image the reference standard. |
| unexpected noise in the baseline | Check grounding and the vibration isolation. |
| drifting focus over a long acquisition | Let the enclosure equilibrate longer. |

## When to stop

Stop and raise a request if the instrument reports a hardware fault code, if a fix would
require opening an enclosure panel, or if the same symptom recurs within one session after
a successful fix. Do not attempt any adjustment that requires tools.

## What to include in the report

The instrument name, the time, the fault code if there is one, and what you had just
changed. A report without the acquisition parameters usually costs a day while staff
reproduce it.
