---
title: "Fusion Cell Sorter Troubleshooting Guide"
version: "1.0"
visibility: public
---

# Fusion Cell Sorter — Troubleshooting Guide

First-line troubleshooting for the Fusion Cell Sorter in the Flow Cytometry Core. Work through this before
raising a fault: roughly half of reported faults on this instrument resolve here.

## Common symptoms

| Symptom | First thing to try |
|---|---|
| sample heating during acquisition | Reduce source power and increase averaging. |
| drifting focus over a long acquisition | Let the enclosure equilibrate longer. |
| no signal at the detector | Check the shutter interlock and the filter path. |
| unexpected noise in the baseline | Check grounding and the vibration isolation. |

## When to stop

Stop and raise a request if the instrument reports a hardware fault code, if a fix would
require opening an enclosure panel, or if the same symptom recurs within one session after
a successful fix. Do not attempt any adjustment that requires tools.

## What to include in the report

The instrument name, the time, the fault code if there is one, and what you had just
changed. A report without the acquisition parameters usually costs a day while staff
reproduce it.
