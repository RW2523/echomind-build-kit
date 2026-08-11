---
title: Asset Management and Preventive Maintenance Standard
version: "2.1"
visibility: public
---

# Asset Management and Preventive Maintenance Standard

Owner: Core Facility Technical Management · Reviewed annually

## 1. What counts as an asset

Any instrument, ancillary unit or shared item of equipment with a facility asset tag. That
includes the instruments users book, and also the things they do not: chillers, compressors,
UPS units, freezers, and the environmental monitoring on the imaging suite.

The ancillary equipment matters as much as the instrument. A confocal is out of service
just as completely when its chiller fails as when its laser does.

## 2. The asset record

Every asset carries a record holding its identifier and location, its service history, its
current status, the responsible core staff member, and its planned maintenance schedule.

The record is the authority on whether an instrument is available. If the record says an
instrument is out of service, it cannot be booked, regardless of whether it looks fine.

## 3. Status

| Status | Bookable | Meaning |
|---|---|---|
| `operational` | Yes | Working normally |
| `degraded` | Yes, with a warning | Usable, with a known limitation shown at booking |
| `maintenance` | No | Planned work in progress |
| `out_of_service` | No | Faulty, awaiting repair |
| `retired` | No | Permanently withdrawn |

`degraded` is the status that earns its place. An instrument with one dead laser line is
not broken, and taking it out of service would deny use to everyone whose work does not
need that line. The limitation is stated at the point of booking so the user can decide.

## 4. Preventive maintenance

Each instrument has a maintenance schedule proportionate to its complexity and criticality.

| Activity | Typical interval |
|---|---|
| Daily performance check | Each day of use, by the first user or staff |
| Calibration verification | Weekly |
| Consumable replacement | As required |
| Vendor preventive service | Annually, or per contract |
| Deep clean and alignment | Quarterly on optical instruments |

Preventive maintenance windows are published at the start of each quarter and appear in the
calendar as ordinary blocks. They take precedence over user bookings, and a booking that
would overlap a published window is refused at the point of booking rather than cancelled
later.

## 5. Unplanned downtime

When an instrument fails:

1. The user ends the session and raises a technical issue immediately.
2. Core staff set the status to `out_of_service`, which cancels affected future bookings
   and notifies those users.
3. The fault, the diagnosis and the fix are recorded against the asset.
4. Charges for the interrupted session are removed without the user needing to dispute.

Downtime is measured from the moment the status changes, not from when the fault was
noticed, so the two are recorded separately: a fault noticed on Friday and reported on
Monday shows three days of undetected fault and however long the repair took.

## 6. Repairs and alignment

Optical alignment is performed by core staff only. This is not a matter of trust; a
misaligned instrument produces plausible data that is quantitatively wrong, and the error
is invisible in the images.

Where a misalignment is traced to a user having adjusted something they should not have,
the realignment is charged to that user's account code. This is the only chargeable
sanction in facility policy, and it exists because realignment on the light sheet takes a
staff member most of a day.

## 7. Vendor contracts and parts

Instruments under a vendor service contract are repaired through that contract. The
facility does not attempt in-house repair on contracted instruments, because doing so can
void the contract for a saving smaller than a single call-out.

Where a repair is not covered, the core manager decides between repair and replacement on
the basis of the asset's remaining expected life, the cost, and the availability of an
alternative in another core.

## 8. Reporting

The asset record produces, per instrument and per quarter: total downtime hours, count of
unplanned failures, mean time to repair, and preventive maintenance compliance.

These are reviewed by core management quarterly and are the basis for replacement cases.
An instrument with rising unplanned failures and lengthening repairs is a replacement case
long before it stops working entirely.

## 9. Retirement

An instrument is retired when it is no longer economic to maintain, when its results can no
longer be validated, or when it is replaced. Retirement sets the status to `retired`,
cancels all future bookings with notice to those users, and preserves the asset record —
including its whole service history — because data produced on that instrument may be
questioned years later.

## 10. Audit trail

Every status change, every maintenance event and every repair is recorded with the person
and the timestamp, and the record cannot be edited after the fact. Corrections are appended.
This is what allows the facility to answer, two years later, whether a given instrument was
within calibration on the day a particular dataset was collected.
