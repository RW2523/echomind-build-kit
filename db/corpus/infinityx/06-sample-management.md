---
title: Sample Management and Chain of Custody Policy
version: "2.0"
visibility: public
---

# Sample Management and Chain of Custody Policy

Owner: Core Facility Operations · Reviewed annually

## 1. Principle

A sample the facility cannot identify is a sample the facility cannot use, and a result
that cannot be traced back to a specific tube is not a result. Every physical item that
enters a core is labelled, logged and tracked from arrival to disposal.

## 2. Identification

Every sample accepted by a core is issued a barcode by the system at the point of
submission. The barcode is the sample's identity for its whole life in the facility.

Your own naming stays with the sample as a user reference and appears alongside the
barcode on every report, but the cores index on the barcode. Two labs each submitting
"control_1" is not a hypothetical; it happens most months.

Labels must be applied to the tube or plate itself, not only to the box. A box arriving
with an unlabelled tube inside is logged as a discrepancy and the sample is held pending
your confirmation of what it is.

## 3. Submission

1. Raise a service request from the appropriate template.
2. The system issues barcodes for the declared number of samples.
3. Print and apply the labels.
4. Deliver the samples to the core's receiving point within the arranged window.
5. Core staff scan each barcode on receipt, which moves the sample to `received`.

Samples delivered without a corresponding request cannot be logged, because there is
nothing to attach them to. They are held at the receiving point and the core attempts to
identify the owner; unclaimed material is disposed of after fourteen days.

## 4. States

| State | Meaning |
|---|---|
| `expected` | Barcode issued, sample not yet arrived |
| `received` | Scanned in at the core |
| `in_storage` | Held in the core's storage pending work |
| `in_process` | Being worked on |
| `analysed` | Work complete, data produced |
| `returned` | Handed back to the submitting lab |
| `disposed` | Destroyed under this policy |

Each transition records who made it and when. That record is the chain of custody and it
is not editable — a mistaken scan is corrected by a further transition with a note, never
by altering history.

## 5. Storage

The cores hold samples only for as long as the work needs. Storage is not a service the
facility offers; a core is not a freezer farm.

| Sample class | Held after analysis | Then |
|---|---|---|
| Routine, non-hazardous | 30 days | Disposed |
| Fixed or embedded material | 90 days | Returned or disposed |
| Nucleic acid extracts | 90 days | Returned or disposed |
| Hazardous or regulated | Per the risk assessment | Per the risk assessment |

You are notified fourteen days before disposal, and again three days before. Ask for return
or extension in that window; extensions are granted where storage exists and are recorded
against the request.

## 6. Return

Return is by arrangement, in person, and is scanned out. The facility does not post
samples and does not leave them for collection unattended.

A returned sample leaves the chain of custody at the point of scanning out. What happens to
it afterwards is the receiving lab's responsibility, and the facility's records will say
only that it was returned, to whom, and when.

## 7. Disposal

Disposal follows the route appropriate to the material and is recorded against the sample.
Regulated material is disposed of through the institution's waste stream with the
associated documentation retained.

Disposal is irreversible and the facility does not accept instructions to dispose of
another lab's material, even from a Group Leader who believes they own it, without the
submitting user's confirmation or a written instruction from the head of the lab.

## 8. Discrepancies

A discrepancy is any mismatch between what was declared and what arrived: a missing tube, an
extra one, an unreadable label, a broken container, a sample at the wrong temperature.

Every discrepancy is logged against the request and you are notified the same day. Work
does not begin on a request with an open discrepancy unless you explicitly confirm that it
should, because proceeding on an assumption about which tube is which is how one lab's
result becomes another lab's.

## 9. Confidentiality

Sample metadata — what a sample is, where it came from, what was done to it — is visible to
the submitting user, their Group Leader, and the core staff handling it. It is not visible
to other labs, and it does not appear in cross-facility reporting except as counts.

## 10. What the facility will not do

- Accept unlabelled material.
- Accept material outside a scheduled window without agreement.
- Store material indefinitely.
- Dispose of material without notice.
- Tell one lab what another lab has submitted.
