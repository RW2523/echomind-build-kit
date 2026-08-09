---
title: "NovaSeq X Troubleshooting Guide"
version: "1.0"
visibility: public
---

# NovaSeq X — Troubleshooting Guide

First-line troubleshooting for the NovaSeq X in the Genomics Core. Work through this before
raising a fault: roughly half of reported faults on this instrument resolve here.

## Common symptoms

| Symptom | First thing to try |
|---|---|
| sample heating during acquisition | Reduce source power and increase averaging. |
| intensity varying between fields | Re-image the reference standard. |
| unexpected noise in the baseline | Check grounding and the vibration isolation. |
| no signal at the detector | Check the shutter interlock and the filter path. |

## When to stop

Stop and raise a request if the instrument reports a hardware fault code, if a fix would
require opening an enclosure panel, or if the same symptom recurs within one session after
a successful fix. Do not attempt any adjustment that requires tools.

## What to include in the report

The instrument name, the time, the fault code if there is one, and what you had just
changed. A report without the acquisition parameters usually costs a day while staff
reproduce it.
