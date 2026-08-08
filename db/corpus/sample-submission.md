---
title: Sample Submission and Tracking
version: "1.1"
visibility: public
---

# Sample Submission and Tracking

## Submitting samples

Samples are submitted against a request template — one template per service, listing the
fields that service needs. Required fields must be complete before the request can be
submitted; the system rejects an incomplete submission rather than queuing it.

Each submitted sample is assigned a barcode of the form `BC` followed by six digits. The
barcode, not your own sample name, is what the facility tracks. Label tubes with the
barcode before drop-off.

## Sample states

A sample moves through five states:

| State | Meaning |
|---|---|
| received | Logged in at the facility |
| in_prep | Being prepared by facility staff |
| on_instrument | Currently being run |
| qc | Undergoing quality control |
| delivered | Results released to the submitter |

States only move forward. A sample that fails QC is not moved backwards; a new sample is
logged against the same request.

## Tracking

Track a sample by its barcode at any time. The tracking view shows the sample's current
state, when it last changed, and the history of the parent request.

## Turnaround

Typical turnaround from `received` to `delivered`:

- Histology sectioning and staining: 5 working days
- Bulk RNA-seq: 15 working days
- Single-cell RNA-seq: 20 working days
- Proteomics: 10 working days

Turnaround is measured in working days from the point the sample reaches `received`, not
from when the request was submitted. A request submitted without its samples arriving
does not start the clock.

## Storage and disposal

Submitted material is retained for 30 days after delivery, then disposed of according to
its hazard class. Ask before submitting if you need material returned.
