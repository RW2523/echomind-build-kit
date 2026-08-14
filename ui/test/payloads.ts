import type { AgentResponse } from "../src/types";

/**
 * Real payloads, captured from the running API, one per shape the chat surface has to
 * cope with. Handwritten fixtures would have agreed with whatever the tests already
 * believed; these disagree where the server disagrees — the usage row whose `rows` field
 * arrives as an empty string, the facilities row whose `instruments` column is a list of
 * objects, the availability answer whose prose contradicts its own free-slot row. Rows
 * are trimmed to three; nothing else is edited.
 *
 * Recapture with: POST /chat as the named user with the question beside each entry.
 */
export interface Captured {
  question: string;
  who: string;
  payload: AgentResponse;
}

export const CAPTURED: Record<string, Captured> = {
  "invoice": {
    "question": "What is on my March invoice?",
    "who": "alice",
    "payload": {
      "response_type": "rows_answer",
      "text": "Your March invoice (inv-ACC-A1-2026-03) includes 7 line items totaling $2689.00. The line items are: Nanopore PromethION usage — 2026-03 ($999.00), Light Sheet LS7 usage — 2026-03 ($476.00), Bioanalyzer B4 usage — 2026-03 ($308.00), Cryo-EM Titan usage — 2026-03 ($290.00), Confocal C2 imaging time — March week 2 ($252.00), Light Sheet LS7 usage — 2026-03 ($204.00), and Confocal C2 imaging time — March week 4 ($160.00).",
      "citations": [],
      "rows": [
        {
          "description": "Nanopore PromethION usage — 2026-03",
          "instrument": "Nanopore PromethION",
          "amount": "999.00"
        },
        {
          "description": "Light Sheet LS7 usage — 2026-03",
          "instrument": "Light Sheet LS7",
          "amount": "476.00"
        },
        {
          "description": "Bioanalyzer B4 usage — 2026-03",
          "instrument": "Bioanalyzer B4",
          "amount": "308.00"
        }
      ],
      "columns": [
        "description",
        "instrument",
        "amount"
      ],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "data",
      "card": null,
      "meta": {
        "model_written": true,
        "plan": {
          "mode": "tool",
          "tool": "get_billing_summary",
          "arguments": {
            "account_code": "ACC-A1",
            "period": "2026-03"
          }
        },
        "result_facts": {
          "account_code": "ACC-A1",
          "period": "2026-03",
          "lab_id": "lab-a",
          "invoice_id": "inv-ACC-A1-2026-03",
          "line_count": 7
        }
      }
    }
  },
  "bookings": {
    "question": "Show me my bookings",
    "who": "alice",
    "payload": {
      "response_type": "rows_answer",
      "text": "You have 20 bookings, all with status completed, under account code ACC-A1. The bookings span from 2026-01-10 to 2026-03-30, using instruments across Mass Spectrometry Core, Genomics Core, and Advanced Imaging Core.",
      "citations": [],
      "rows": [
        {
          "id": "bk-0133",
          "instrument": "MALDI-TOF R2",
          "facility": "Mass Spectrometry Core",
          "starts_at": "2026-03-30T01:00:00+00:00",
          "ends_at": "2026-03-30T02:30:00+00:00",
          "status": "completed",
          "account_code": "ACC-A1"
        },
        {
          "id": "bk-0140",
          "instrument": "Light Sheet LS7",
          "facility": "Advanced Imaging Core",
          "starts_at": "2026-03-28T00:00:00+00:00",
          "ends_at": "2026-03-28T04:00:00+00:00",
          "status": "completed",
          "account_code": "ACC-A1"
        },
        {
          "id": "bk-0011",
          "instrument": "MALDI-TOF R2",
          "facility": "Mass Spectrometry Core",
          "starts_at": "2026-03-21T18:30:00+00:00",
          "ends_at": "2026-03-21T22:30:00+00:00",
          "status": "completed",
          "account_code": "ACC-A1"
        }
      ],
      "columns": [
        "id",
        "instrument",
        "facility",
        "starts_at",
        "ends_at",
        "status",
        "account_code"
      ],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "data",
      "card": null,
      "meta": {
        "model_written": true,
        "plan": {
          "mode": "tool",
          "tool": "get_my_bookings",
          "arguments": {}
        },
        "result_facts": {
          "user_id": "u-alice",
          "count": 20
        }
      }
    }
  },
  "facilities": {
    "question": "Where is the nearest core that can do cryo-EM?",
    "who": "alice",
    "payload": {
      "response_type": "rows_answer",
      "text": "The nearest core that can do cryo-EM is the Advanced Imaging Core (IMG) on North Campus, Wellcome Building, Level 2, Rooms 2.10-2.24. It has a Cryo-EM Titan instrument available for booking.",
      "citations": [],
      "rows": [
        {
          "id": "fac-imaging",
          "name": "Advanced Imaging Core",
          "code": "IMG",
          "campus": "North Campus",
          "building": "Wellcome Building",
          "room": "Level 2, Rooms 2.10-2.24",
          "address": "14 Rutherford Way, North Campus",
          "contact_email": "imaging-core@example.edu",
          "opening_hours": "08:00-20:00 Mon-Fri",
          "latitude": "51.52",
          "longitude": "-0.13",
          "instruments": [
            {
              "id": "ins-em-titan",
              "name": "Cryo-EM Titan",
              "hourly_rate": 145.0,
              "status": "available",
              "bookable": true,
              "modality": "electron microscopy",
              "techniques": [
                "cryo-EM",
                "single particle analysis",
                "cryo-electron tomography",
                "structural biology"
              ],
              "sample_types": [
                "vitrified grids",
                "protein complexes",
                "virus particles"
              ],
              "specification": "300 kV cryo-TEM, direct electron detector, single-particle and tomography workflows.",
              "room": "2.24"
            }
          ]
        }
      ],
      "columns": [
        "id",
        "name",
        "code",
        "campus",
        "building",
        "room",
        "address",
        "contact_email",
        "opening_hours",
        "latitude",
        "longitude",
        "instruments"
      ],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "data",
      "card": {
        "kind": "facilities",
        "title": "Facilities that do cryo-em",
        "subtitle": null,
        "fields": [
          {
            "label": "Technique",
            "value": "cryo-em",
            "emphasis": false
          },
          {
            "label": "Facilities",
            "value": "1",
            "emphasis": true
          },
          {
            "label": "Instruments",
            "value": "1",
            "emphasis": false
          }
        ],
        "items": [
          {
            "title": "Advanced Imaging Core",
            "subtitle": "North Campus · Wellcome Building",
            "meta": [
              "1 instrument",
              "Level 2, Rooms 2.10-2.24",
              "14 Rutherford Way, North Campus",
              "08:00-20:00 Mon-Fri",
              "imaging-core@example.edu"
            ],
            "badges": [
              {
                "text": "Cryo-EM Titan · available",
                "tone": "ok"
              }
            ],
            "value": "1 instrument"
          }
        ],
        "footer": null
      },
      "meta": {
        "model_written": true,
        "plan": {
          "mode": "tool",
          "tool": "find_facilities",
          "arguments": {
            "technique": "cryo-em"
          }
        },
        "result_facts": {
          "technique": "cryo-em",
          "campus": null,
          "origin": null,
          "matched": 1,
          "matched_instruments": 1
        }
      }
    }
  },
  "instruments": {
    "question": "I want to image live cells — what should I use?",
    "who": "alice",
    "payload": {
      "response_type": "rows_answer",
      "text": "Confocal C2, Confocal C3 and Spinning Disk SD1 can be used for live-cell imaging. Confocal C2 and Confocal C3 support live-cell imaging with confocal microscopy and FRAP, while Spinning Disk SD1 offers live-cell imaging with high-speed timelapse capabilities.",
      "citations": [],
      "rows": [
        {
          "instrument": "Confocal C2",
          "facility": "Advanced Imaging Core",
          "campus": "North Campus",
          "building": "Wellcome Building",
          "room": "2.10",
          "hourly_rate": "42.00",
          "status": "available",
          "bookable": true,
          "modality": "light microscopy",
          "techniques": [
            "confocal microscopy",
            "immunofluorescence",
            "live-cell imaging",
            "colocalisation"
          ],
          "sample_types": [
            "fixed cells",
            "live cells",
            "tissue sections"
          ],
          "specification": "Point-scanning confocal, 405/488/561/640 nm, 63x oil, resolution ~180 nm lateral.",
          "contact_email": "imaging-core@example.edu",
          "score": 3,
          "why_matched": [
            "techniques match: imag"
          ]
        },
        {
          "instrument": "Confocal C3",
          "facility": "Advanced Imaging Core",
          "campus": "North Campus",
          "building": "Wellcome Building",
          "room": "2.12",
          "hourly_rate": "46.00",
          "status": "available",
          "bookable": true,
          "modality": "light microscopy",
          "techniques": [
            "confocal microscopy",
            "immunofluorescence",
            "FRAP",
            "live-cell imaging"
          ],
          "sample_types": [
            "fixed cells",
            "live cells",
            "organoids"
          ],
          "specification": "Point-scanning confocal with FRAP module and environmental chamber, 37C and CO2.",
          "contact_email": "imaging-core@example.edu",
          "score": 3,
          "why_matched": [
            "techniques match: imag"
          ]
        },
        {
          "instrument": "Spinning Disk SD1",
          "facility": "Advanced Imaging Core",
          "campus": "North Campus",
          "building": "Wellcome Building",
          "room": "2.14",
          "hourly_rate": "55.00",
          "status": "available",
          "bookable": true,
          "modality": "light microscopy",
          "techniques": [
            "spinning disk confocal",
            "live-cell imaging",
            "high-speed timelapse"
          ],
          "sample_types": [
            "live cells",
            "organoids",
            "zebrafish embryos"
          ],
          "specification": "Spinning disk, sCMOS camera, up to 100 fps, low phototoxicity for long timelapse.",
          "contact_email": "imaging-core@example.edu",
          "score": 3,
          "why_matched": [
            "techniques match: imag"
          ]
        }
      ],
      "columns": [
        "instrument",
        "facility",
        "campus",
        "building",
        "room",
        "hourly_rate",
        "status",
        "bookable",
        "modality",
        "techniques",
        "sample_types",
        "specification",
        "contact_email",
        "score",
        "why_matched"
      ],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "data",
      "card": {
        "kind": "instruments",
        "title": "Recommended instruments",
        "subtitle": "image",
        "fields": [
          {
            "label": "Goal",
            "value": "image",
            "emphasis": false
          },
          {
            "label": "Sample type",
            "value": "live cells",
            "emphasis": false
          },
          {
            "label": "Matches",
            "value": "3",
            "emphasis": true
          },
          {
            "label": "Best match",
            "value": "Confocal C2",
            "emphasis": true
          }
        ],
        "items": [
          {
            "title": "Confocal C2",
            "subtitle": "Advanced Imaging Core",
            "meta": [
              "$42.00/h",
              "North Campus",
              "Wellcome Building",
              "2.10",
              "techniques match: imag"
            ],
            "badges": [
              {
                "text": "available",
                "tone": "ok"
              },
              {
                "text": "light microscopy",
                "tone": "info"
              }
            ],
            "value": "score 3"
          },
          {
            "title": "Confocal C3",
            "subtitle": "Advanced Imaging Core",
            "meta": [
              "$46.00/h",
              "North Campus",
              "Wellcome Building",
              "2.12",
              "techniques match: imag"
            ],
            "badges": [
              {
                "text": "available",
                "tone": "ok"
              },
              {
                "text": "light microscopy",
                "tone": "info"
              }
            ],
            "value": "score 3"
          },
          {
            "title": "Spinning Disk SD1",
            "subtitle": "Advanced Imaging Core",
            "meta": [
              "$55.00/h",
              "North Campus",
              "Wellcome Building",
              "2.14",
              "techniques match: imag"
            ],
            "badges": [
              {
                "text": "available",
                "tone": "ok"
              },
              {
                "text": "light microscopy",
                "tone": "info"
              }
            ],
            "value": "score 3"
          }
        ],
        "footer": "Ranked by recorded capability, not by availability — check each status."
      },
      "meta": {
        "model_written": true,
        "plan": {
          "mode": "tool",
          "tool": "recommend_instrument",
          "arguments": {
            "goal": "image",
            "sample_type": "live cells"
          }
        },
        "result_facts": {
          "goal": "image",
          "sample_type": "live cells",
          "matched": 3,
          "excluded_by_sample_type": 1
        }
      }
    }
  },
  "availability": {
    "question": "Is the Confocal C2 free on 2026-03-18?",
    "who": "alice",
    "payload": {
      "response_type": "rows_answer",
      "text": "The Confocal C2 is not free on 2026-03-18. It is booked from 2026-03-18T08:00:00+00:00 to 2026-03-18T20:00:00+00:00.",
      "citations": [],
      "rows": [
        {
          "free_from": "2026-03-18T08:00:00+00:00",
          "free_until": "2026-03-18T20:00:00+00:00"
        }
      ],
      "columns": [
        "free_from",
        "free_until"
      ],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "data",
      "card": null,
      "meta": {
        "model_written": true,
        "plan": {
          "mode": "tool",
          "tool": "check_availability",
          "arguments": {
            "instrument_id": "ins-confocal-c2",
            "date_from": "2026-03-18",
            "date_to": "2026-03-18"
          }
        },
        "result_facts": {
          "instrument_id": "ins-confocal-c2",
          "instrument_name": "Confocal C2",
          "instrument_status": "available",
          "instrument_facility": "Advanced Imaging Core",
          "instrument_hourly_rate": 42.0,
          "requested_window": "2026-03-18T00:00:00+00:00 to 2026-03-19T00:00:00+00:00",
          "requested_window_free": true,
          "bookable": true,
          "unavailable_reason": null,
          "conflicting_bookings": 0,
          "window_from": "2026-03-18T00:00:00+00:00",
          "window_to": "2026-03-19T00:00:00+00:00",
          "opening_hours": "08:00-20:00 UTC",
          "busy": ""
        }
      }
    }
  },
  "policy": {
    "question": "What am I charged if I cancel a booking 12 hours before it starts?",
    "who": "alice",
    "payload": {
      "response_type": "answer",
      "text": "If you cancel a booking 12 hours before it starts, you are charged 50% of the booked time [1]. This is based on the cancellation policy which states that cancellations within 24 hours of start time result in a 50% charge [2].",
      "citations": [
        {
          "index": 1,
          "doc_id": "doc-booking-and-cancellation-rules-v1-6",
          "breadcrumb": "Booking and Cancellation Rules > Session limits, Fair-share limits, Cancellation (v1.6)",
          "title": "Booking and Cancellation Rules",
          "chunk_id": 2650,
          "score": 0.6583
        },
        {
          "index": 2,
          "doc_id": "doc-core-facility-general-policies-v2-1",
          "breadcrumb": "Core Facility General Policies > Cancellation and no-shows, Acknowledgement, Contact (v2.1)",
          "title": "Core Facility General Policies",
          "chunk_id": 2656,
          "score": 0.7068
        }
      ],
      "rows": [],
      "columns": [],
      "executed_sql": null,
      "pending_action": null,
      "gate": {
        "passed": true,
        "reason": "ok",
        "top_score": 0.7068215120254121,
        "coverage": true,
        "agreement": true,
        "conflict_checked": true,
        "closest_breadcrumb": "Booking and Cancellation Rules > Session limits, Fair-share limits, Cancellation (v1.6)",
        "thresholds": {
          "min_top_score": 0.45,
          "min_agreement": 2,
          "reranker": "bge"
        }
      },
      "faithfulness": {
        "passed": true,
        "score": 1.0,
        "checked": 2,
        "unsupported": [],
        "verdicts": [
          {
            "claim": "If you cancel a booking 12 hours before it starts, you are charged 50% of the booked time .",
            "cited": [
              1
            ],
            "supported": true,
            "why": "Sources state 12 hours before start is 50% charge",
            "corrected_source": null
          },
          {
            "claim": "This is based on the cancellation policy which states that cancellations within 24 hours of start time result in a 50% charge .",
            "cited": [
              2
            ],
            "supported": true,
            "why": "Sources state 24 hours of start time is 50% charge",
            "corrected_source": null
          }
        ],
        "corrections": []
      },
      "route": "knowledge",
      "card": null,
      "meta": {}
    }
  },
  "documentProposal": {
    "question": "Generate the monthly summary for 2026-03",
    "who": "cora",
    "payload": {
      "response_type": "approval_request",
      "text": "I've prepared this, but I haven't done it. Generate monthly summary as PDF (month 2026-03).\n\nApprove it and I'll execute it; decline and nothing happens. Either way it goes in the audit log.",
      "citations": [],
      "rows": [],
      "columns": [],
      "executed_sql": null,
      "pending_action": {
        "action_id": "act-395e257ec002",
        "status": "pending",
        "kind": "document",
        "tool": "generate_document",
        "payload_preview": "Generate monthly summary as PDF (month 2026-03)",
        "payload": {
          "template": "monthly_summary",
          "params": {
            "account_code": null,
            "month": "2026-03"
          },
          "format": "pdf"
        },
        "message": "This needs your approval before anything changes. Nothing has been written yet."
      },
      "gate": null,
      "faithfulness": null,
      "route": "action",
      "card": null,
      "meta": {
        "plan": {
          "tool": "generate_document",
          "arguments": {
            "template": "monthly_summary",
            "params": {
              "account_code": null,
              "month": "2026-03"
            }
          }
        }
      }
    }
  },
  "downtimeSql": {
    "question": "Which instrument had the most downtime in March 2026?",
    "who": "cora",
    "payload": {
      "response_type": "rows_answer",
      "text": "Light Sheet LS7 had the most downtime in March 2026, with a total downtime of 69.00 hours.",
      "citations": [],
      "rows": [
        {
          "instrument": "Light Sheet LS7",
          "total_downtime": "69.00"
        }
      ],
      "columns": [
        "instrument",
        "total_downtime"
      ],
      "executed_sql": "SELECT instrument, SUM(downtime_hours) AS total_downtime FROM v_instrument_downtime WHERE month = '2026-03' GROUP BY instrument ORDER BY total_downtime DESC LIMIT 1",
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "data",
      "card": null,
      "meta": {
        "model_written": true,
        "plan": {
          "mode": "sql",
          "sql": "SELECT instrument, SUM(downtime_hours) AS total_downtime FROM v_instrument_downtime WHERE month = '2026-03' GROUP BY instrument ORDER BY total_downtime DESC LIMIT 1"
        },
        "result_facts": {}
      }
    }
  },
  "labUsage": {
    "question": "Show me lab A's usage this month",
    "who": "asha",
    "payload": {
      "response_type": "rows_answer",
      "text": "Lab A has 0 scheduled hours, 0 tracked hours, and 0 difference hours for August 2026.",
      "citations": [],
      "rows": [
        {
          "scope": "lab",
          "id": "lab-a",
          "month": "2026-08",
          "rows": "",
          "scheduled_hours": 0,
          "tracked_hours": 0,
          "difference_hours": 0
        }
      ],
      "columns": [
        "scope",
        "id",
        "month",
        "rows",
        "scheduled_hours",
        "tracked_hours",
        "difference_hours"
      ],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "data",
      "card": null,
      "meta": {
        "model_written": true,
        "plan": {
          "mode": "tool",
          "tool": "get_usage_records",
          "arguments": {
            "scope": "lab",
            "id": "lab-a",
            "month": "2026-08"
          }
        },
        "result_facts": {}
      }
    }
  },
  "smalltalk": {
    "question": "hello",
    "who": "alice",
    "payload": {
      "response_type": "smalltalk",
      "text": "I'm EchoMind, the assistant for this core facility. I can answer questions about facility policies and SOPs, look up your bookings, usage and invoices, track samples and requests, and prepare bookings or documents for your approval. Everything I tell you comes from the platform's own records — if I can't verify something, I'll say so.",
      "citations": [],
      "rows": [],
      "columns": [],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "smalltalk",
      "card": null,
      "meta": {}
    }
  },
  "outOfScope": {
    "question": "what is the weather in paris",
    "who": "alice",
    "payload": {
      "response_type": "scope",
      "text": "I only cover the Infinity X core-facility platform. I can help with facility policies and SOPs, instrument availability and bookings, training requirements, service requests and sample tracking, usage and billing records, projects, and generating facility documents. Ask me something in that world and I'll answer it from verified records.",
      "citations": [],
      "rows": [],
      "columns": [],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "out_of_scope",
      "card": null,
      "meta": {}
    }
  },
  "refusal": {
    "question": "What would it cost me to cancel booking bk-0133?",
    "who": "alice",
    "payload": {
      "response_type": "redirect",
      "text": "You do not have access to this resource. I can only show you records you are entitled to see, and this is not one of them. Ask the core facility admin if you believe you should have access.",
      "citations": [],
      "rows": [],
      "columns": [],
      "executed_sql": null,
      "pending_action": null,
      "gate": null,
      "faithfulness": null,
      "route": "data",
      "card": null,
      "meta": {
        "error": {
          "code": "forbidden",
          "message": "You do not have access to this resource.",
          "hint": "Ask the core facility admin if you believe you should have access."
        }
      }
    }
  }
} as unknown as Record<string, Captured>;
