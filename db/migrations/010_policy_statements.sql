-- The rules the application actually applies, as data.
--
-- Every value here is transcribed from a document in the corpus, and every row names the
-- document and the clause it came from. Nothing in this file is a rounding, a judgement
-- call, or a number that seemed reasonable: if the prose does not state a figure, the
-- column is NULL and the rule is prose-only. That is the whole discipline — a decision
-- that says "cancelled free of charge" must be able to show the sentence that made it
-- free, and a sentence with no number cannot be turned into one here.
--
-- When the prose changes, this table is wrong until it is changed too. `source_doc_id`
-- and `version` are what make that detectable rather than silent.

INSERT INTO policy.statements
    (id, domain, subject, title, statement, threshold_hours, charge_percent,
     applies_to, source_doc_id, source_clause, effective_from, version)
VALUES
-- Booking and Cancellation Rules v1.6, "Cancellation" — the three-row table.
('pol-cancel-free',
 'booking', 'cancellation',
 'Cancelling more than 24 hours ahead',
 'A booking cancelled more than 24 hours before it starts is not charged.',
 24, 0, 'all',
 'doc-booking-and-cancellation-rules-v1-6', 'Cancellation',
 '2026-01-01', '1.6'),

('pol-cancel-late',
 'booking', 'cancellation',
 'Cancelling within 24 hours',
 'A booking cancelled within 24 hours of its start is charged at 50% of the booked time.',
 24, 50, 'all',
 'doc-booking-and-cancellation-rules-v1-6', 'Cancellation',
 '2026-01-01', '1.6'),

('pol-no-show',
 'booking', 'no_show',
 'No-show',
 'A booking not started within 30 minutes is a no-show and is charged at 100% of the '
 'booked time. It is also recorded against the user.',
 0.5, 100, 'all',
 'doc-booking-and-cancellation-rules-v1-6', 'Cancellation',
 '2026-01-01', '1.6'),

-- Scheduling Operations Guide v4.1 §4 — moving a booking is not a special case.
('pol-reschedule',
 'booking', 'reschedule',
 'Moving a booking',
 'A booking may be moved at any time before it starts. Moving it is treated as a '
 'cancellation plus a new booking, so the ordinary cancellation charge applies to the '
 'released time.',
 NULL, NULL, 'all',
 'doc-scheduling-operations-guide-v4-1', '4. Changing a booking',
 '2026-01-01', '4.1'),

('pol-shorten',
 'booking', 'reschedule',
 'Shortening a booking',
 'A booking may be shortened at any time before it starts. The released time follows the '
 'ordinary cancellation rules.',
 NULL, NULL, 'all',
 'doc-scheduling-operations-guide-v4-1', '4. Changing a booking',
 '2026-01-01', '4.1'),

-- Booking and Cancellation Rules v1.6, "Session limits" and "Fair-share limits".
('pol-session-length',
 'booking', 'limits',
 'Maximum session length',
 'A single booking may not exceed 12 hours. Longer experiments are booked as consecutive '
 'sessions and need the facility manager''s approval.',
 12, NULL, 'all',
 'doc-booking-and-cancellation-rules-v1-6', 'Session limits',
 '2026-01-01', '1.6'),

('pol-booking-horizon',
 'booking', 'limits',
 'How far ahead a booking can be made',
 'Bookings can be made up to 60 days ahead. There is no minimum notice: a free slot can '
 'be taken at any time.',
 1440, NULL, 'all',
 'doc-booking-and-cancellation-rules-v1-6', 'Session limits',
 '2026-01-01', '1.6'),

('pol-fair-share',
 'booking', 'limits',
 'Peak-hours fair-share cap',
 'During peak hours (09:00-17:00 UTC, Monday to Friday) one user may hold at most 16 '
 'booked hours per instrument per week. Outside peak hours there is no cap. The limit is '
 'per user, not per lab.',
 16, NULL, 'all',
 'doc-booking-and-cancellation-rules-v1-6', 'Fair-share limits',
 '2026-01-01', '1.6'),

('pol-cancel-channel',
 'booking', 'cancellation',
 'How a cancellation must be made',
 'Cancellations are made through the scheduling system. A cancellation by email or in '
 'person does not count — only the system record does.',
 NULL, NULL, 'all',
 'doc-booking-and-cancellation-rules-v1-6', 'Cancellation',
 '2026-01-01', '1.6'),

-- Recharge, Billing and Invoice Approval Policy v3.3 §4.
('pol-invoice-review',
 'billing', 'review',
 'Invoice review period',
 'The review period is 14 days from the date the invoice was issued. After it ends the '
 'invoice commits whether or not it was reviewed.',
 336, NULL, 'all',
 'doc-recharge-billing-and-invoice-approval-policy-v3-3', '4. Review and approval',
 '2026-01-01', '3.3'),

-- Instrument Scheduling and Cancellation Policy v4.0 — out-of-service handling.
('pol-out-of-service',
 'booking', 'cancellation',
 'Bookings on an instrument taken out of service',
 'Bookings on an instrument that goes out of service are cancelled automatically and are '
 'not charged.',
 NULL, 0, 'all',
 'doc-instrument-scheduling-and-cancellation-policy-v4-0', 'Instrument status',
 '2026-01-01', '4.0')

ON CONFLICT (id) DO UPDATE SET
    domain          = EXCLUDED.domain,
    subject         = EXCLUDED.subject,
    title           = EXCLUDED.title,
    statement       = EXCLUDED.statement,
    threshold_hours = EXCLUDED.threshold_hours,
    charge_percent  = EXCLUDED.charge_percent,
    applies_to      = EXCLUDED.applies_to,
    source_doc_id   = EXCLUDED.source_doc_id,
    source_clause   = EXCLUDED.source_clause,
    effective_from  = EXCLUDED.effective_from,
    version         = EXCLUDED.version,
    updated_at      = now();
