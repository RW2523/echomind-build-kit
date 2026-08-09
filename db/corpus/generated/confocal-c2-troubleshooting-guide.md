---
title: "Confocal C2 Troubleshooting Guide"
version: "1.0"
visibility: public
---

# Confocal C2 — Troubleshooting Guide

First-line troubleshooting for the Confocal C2 in the Advanced Imaging Core. Work through this before
raising a fault: roughly half of reported faults on this instrument resolve here.

## Common symptoms

| Symptom | First thing to try |
|---|---|
| no signal at the detector | Check the shutter interlock and the filter path. |
| drifting focus over a long acquisition | Let the enclosure equilibrate longer. |
| unexpected noise in the baseline | Check grounding and the vibration isolation. |
| intensity varying between fields | Re-image the reference standard. |

## When to stop

Stop and raise a request if the instrument reports a hardware fault code, if a fix would
require opening an enclosure panel, or if the same symptom recurs within one session after
a successful fix. Do not attempt any adjustment that requires tools.

## What to include in the report

The instrument name, the time, the fault code if there is one, and what you had just
changed. A report without the acquisition parameters usually costs a day while staff
reproduce it.
