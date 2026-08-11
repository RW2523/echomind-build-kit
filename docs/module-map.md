# Infinity X module map

Which part of the core-facility platform each tool answers for. Fifteen tools, eleven
reads and four writes, and every one of them is registered in `server/mcp/tools.py` —
the table below is generated against that registry, so it cannot drift into describing
tools that do not exist.

<!-- generated: scripts/module_map.py -->

| # | Tool | Infinity X module | Tier | What it answers |
|---:|---|---|---|---|
| 1 | `get_user_profile` | Identity & training records | T1 self / T2 lab / T3 | Who am I, which lab, what am I trained on, which account codes |
| 2 | `get_facility_catalog` | Instrument catalogue & rates | T0 | What instruments exist, in which core, at what hourly rate |
| 3 | `check_availability` | Scheduling — availability | T0 | When is this instrument free between two dates |
| 4 | `get_my_bookings` | Scheduling — bookings | T1 | What have I booked, and what is its status |
| 5 | `get_usage_records` | Usage analytics | T1/T2 | Scheduled versus tracked hours, by user, lab or instrument |
| 6 | `get_request_status` | Service requests | T1/T2 | Where has my request got to, and what happened to it |
| 7 | `track_sample` | Sample tracking | T1/T2 | Where is this barcode now, and what has been done to it |
| 8 | `get_billing_summary` | Billing & invoicing | T1 own / T2 / T3 | What was this account charged this period, line by line |
| 9 | `get_project_overview` | Projects | T2/T3 | Who is on this project, which cores it uses, what it has spent |
| 10 | `get_instrument_health` | Instrument health | T0 status / T3 history | Is it up, when was it last serviced, how much downtime |
| 11 | `run_readonly_sql` | Reporting | T2/T3 | Anything else, in natural language, over four allow-listed views |
| 12 | `create_onboarding_request` | Onboarding (write) | T1 | Get this person an account, with the PI's acknowledgement |
| 13 | `create_service_request` | Service requests (write) | T1 | Raise this request from a template or an uploaded form |
| 14 | `request_booking` | Scheduling — booking (write) | T1 | Book this instrument for me — proposed, never executed unasked |
| 15 | `generate_document` | Reporting documents (write) | T1 / T3 admin templates | Produce a usage report, onboarding packet or monthly summary |

15 tools registered.
