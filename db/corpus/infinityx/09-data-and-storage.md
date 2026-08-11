---
title: Research Data Handling and Cloud Storage Standard
version: "1.4"
visibility: public
---

# Research Data Handling and Cloud Storage Standard

Owner: Core Facility Data Management · Reviewed annually

## 1. Three places data lives

Data produced in the facility passes through three distinct places, with different
guarantees. Confusing them is the most common cause of data loss here.

| Place | What it is | Backed up | Guarantee |
|---|---|---|---|
| Instrument PC | Working space attached to an instrument | No | None — cleared on a schedule |
| Facility transfer share | Staging area for moving data out | No | Short-term availability only |
| Institutional research storage | The lab's own managed storage | Yes | Per the institution's policy |

Only the third is storage. The first two are transit. The specific retention periods for
the instrument PCs and the transfer share are set out in the Data Management and Retention
document and are not repeated here, so that there is one authority rather than two.

## 2. Getting data off an instrument

The transfer share is the route. Personal drives, memory sticks and phones must never be
attached to an acquisition machine — not because of the data on them, but because the
acquisition machines run vendor software that cannot be patched freely and are kept off the
general network for that reason.

Copy at the end of every session. This is step one of every shutdown procedure in the
facility, and it exists because the alternative — discovering at the end of a project that
a month of acquisitions expired — is unrecoverable.

## 3. What travels with the data

Acquisition data is only interpretable with its acquisition settings. The cores write those
settings alongside the data automatically where the instrument supports it, and in a
sidecar file where it does not.

Do not separate them. A figure panel whose acquisition settings are unknown cannot be
reproduced, cannot be compared against a later experiment, and in practice cannot be
defended if it is questioned.

## 4. Naming

The cores do not impose a naming scheme on lab data, because every lab has one and
imposing another produces two. Two requirements only:

- The name must contain the sample barcode or the service request id, so the data can be
  traced back to the physical material.
- The name must not contain personal identifiers of human subjects. Where human material is
  involved, the barcode is the only identifier that may appear.

## 5. Human and sensitive material

Data derived from human subjects is handled under the institution's research ethics
approvals, which take precedence over this standard wherever they differ.

The facility holds such data only for the minimum period needed to complete the work, and
core staff access it only to perform the requested work. It is excluded from the cores'
routine reporting entirely, including counts.

## 6. Access

Data on the transfer share is visible to the submitting user, their Group Leader, and the
core staff working on the request. Not to other labs, and not to core staff in other cores.

A Group Leader can request access to a departed group member's data. The request is
recorded and the data is made available to the Group Leader, because the data belongs to
the lab rather than to the individual.

## 7. Deletion

Deleting from the transfer share is immediate and irreversible. There is no recycle bin and
no snapshot. Before deleting, confirm the copy in institutional storage opens.

The facility deletes on schedule regardless of whether you have copied the data. Notices
are sent, but the schedule is not contingent on the notice being read.

## 8. Uploads to the assistant

Documents uploaded by a user to the facility assistant are private to that user. They are
retrievable in that user's own conversations and nowhere else — not by their Group Leader,
not by core staff, not by an administrator.

Deleting an upload removes its indexed content as well as the file. There is no separate
step and no delay.

Do not upload research data itself. The assistant is for documents — protocols, forms,
notes — and the upload area is neither backed up nor sized for acquisition data.

## 9. Publication and archiving

Where a journal or funder requires data deposition, that is the lab's responsibility and
the facility takes no part in it beyond providing the acquisition metadata on request.

The facility can produce, for a given publication, the list of instruments used, the dates
of the sessions, and the acquisition settings recorded at the time. Ask through a service
request, quoting the project.
