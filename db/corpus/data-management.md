---
title: Data Management and Retention
version: "1.3"
visibility: public
---

# Data Management and Retention

## Instrument PCs are not storage

Data written to an instrument PC is deleted 30 days after acquisition, automatically and
without warning. Instrument PCs are working space, not an archive. Copy your data off at
the end of every session — this is step 1 of every shutdown procedure in the facility for
exactly this reason.

## Facility transfer share

Each core exposes a transfer share for moving data off the instrument. Data on the
transfer share is retained for 90 days, then deleted. The share is not backed up.

Nothing in the facility constitutes a backup of your data. Institutional research storage
is the correct destination, and moving data there is the user's responsibility.

## File naming

The facility asks for a consistent convention so that reconciliation between billing
records and acquired data is possible:

    YYYYMMDD_<accountcode>_<instrument>_<sampleid>_<n>

This is a request, not an enforced rule, but it materially speeds up any billing dispute
because the acquisition record can be matched to the invoice line directly.

## Sensitive data

Human-derived data with any identifying content must not be written to an instrument PC
or transfer share at all. Acquire to an encrypted external volume, and speak to the
facility manager before the session so the instrument can be configured appropriately.

## Retention summary

| Location | Retention | Backed up |
|---|---|---|
| Instrument PC | 30 days | No |
| Facility transfer share | 90 days | No |
| Institutional research storage | Per institutional policy | Yes |

## Requesting recovery

Deleted data is not recoverable. There is no undelete on either the instrument PCs or the
transfer share. A recovery request can only confirm the deletion date from the log.
