---
title: Service Request and Work Order Handbook
version: "2.2"
visibility: public
---

# Service Request and Work Order Handbook

Owner: Core Facility Operations · Reviewed twice yearly

## 1. What a service request is

A service request is work the core does *for* you, as distinct from instrument time, which
is work you do yourself. Sample preparation, a sequencing run, a staff-operated imaging
session, data analysis support and method development are all service requests.

If you are touching the instrument, it is a booking. If the core is, it is a service
request. Work that starts as one and becomes the other — a user who begins a session and
asks staff to take over — is recorded as both, split at the handover.

## 2. Raising a request

Every request starts from a template. Templates exist per core and per work type, and each
one asks only for what that work genuinely needs: a sequencing request asks for read
length and target depth; a staff-operated imaging request asks for the objective and the
channels.

Fill the template completely. The single largest cause of delay is a request that arrives
missing a field the core must have, which then waits for an exchange of messages before
any work can begin.

You may also raise a request by uploading a completed submission form. The form is read,
the fields are extracted into a draft request, and you are shown the draft to confirm
before it is submitted. Nothing is submitted on your behalf without that confirmation.

## 3. The lifecycle

| Status | Meaning | Who moves it on |
|---|---|---|
| `draft` | Being written; not visible to the core | You |
| `submitted` | With the core, not yet accepted | Core staff |
| `accepted` | Scheduled; samples may be delivered | Core staff |
| `in_progress` | Work has begun | Core staff |
| `on_hold` | Waiting on you — a missing sample, an unanswered question | You |
| `completed` | Work finished, results available | — |
| `cancelled` | Stopped before completion | You or core staff |

A request can be edited freely while it is `draft` or `submitted`. Once it moves to
`accepted`, raise a new request referencing the old one rather than editing in place, so
that the history of what was agreed stays intact.

## 4. Turnaround

Requests raised through the system are triaged the next working day. Anything blocking an
instrument is looked at the same day.

Turnaround after acceptance depends on the work and is quoted per request. Published
targets, which the cores meet or better in the large majority of cases:

| Work type | Target from acceptance |
|---|---|
| Standard sample QC | 3 working days |
| Staff-operated imaging session | 10 working days |
| Standard sequencing run | 15 working days |
| Method development | Quoted individually |

A request that will miss its target is flagged to you before the target passes, not after.

## 5. On hold

A request goes `on_hold` when the core cannot proceed without something from you. The
reason is always recorded and you are notified.

A request that stays `on_hold` for thirty days is cancelled, and any samples held against
it are handled under the sample retention rules. This is not a threat; it is how the core
avoids a freezer full of material for work nobody intends to finish.

## 6. Charging

A service request is charged on completion, not on submission, and covers:

- staff time at the facility rate, recorded against the request;
- instrument time consumed by the work, at the instrument rate;
- consumables issued, at stockroom prices;
- any per-sample fee published for that template.

A cancelled request is charged for work already performed. Where the core has not yet
accepted it, nothing is charged.

The notice period that applies to instrument bookings does not apply here: a service
request has no reserved slot to release, so there is no late-cancellation fee. The two
rules are separate and are often confused.

## 7. Priority

The cores do not operate a general priority queue, because a queue everyone can jump is
not a queue. Two exceptions:

- **Time-critical biological material** — samples that degrade — is scheduled ahead of
  routine work by the core manager, on request, with the reason recorded.
- **Repeat work after a core-caused failure** goes to the front and is not charged again.

## 8. Results and data

Results are delivered to the facility transfer share and you are notified. Large datasets
are delivered as a manifest plus a link rather than as an attachment.

The core keeps a copy of results for ninety days after completion so that a delivery
problem can be resolved. After that the core's copy is removed and the only copy is yours.
Move your data promptly; this is the most common cause of genuine, irrecoverable loss in
the facility.

## 9. Working across two cores

Raise a request in each core. They are tracked separately because each core schedules its
own work, but reference the other request in both so staff can sequence them.

The Project Management route is the better tool where this happens repeatedly: a project
groups the requests, the bookings and the spend across cores into one view.

## 10. Complaints about a completed request

Raise it with the core manager within thirty days, with the request id. Where the core
agrees the work was not to standard, it is repeated without charge. Where the disagreement
is about what was agreed rather than what was delivered, the request record — including
the template as submitted — is what the core will refer to.
