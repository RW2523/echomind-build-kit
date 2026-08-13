"""Create the schema, views and roles, then load the deterministic mock dataset.

Run with `make seed`. Idempotent: it truncates the mock data and rewrites it, so the
row counts and the demo story are identical on every run.

Determinism note: the dataset is anchored to a fixed reference date rather than "now".
Spec 01 asks for both a seeded RNG *and* a March billing story; anchoring keeps the
March invoices inside the 90-day activity window forever, so the demo and its tests
never depend on the day they are run.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from server.config import REPO_ROOT, settings
from server.db import owner_engine as engine
from server.demo_identities import DEMO_USERS

MIGRATIONS = REPO_ROOT / "db" / "migrations"

# Where LangGraph's checkpoint tables live. Pinned so it never depends on role names.
CHECKPOINT_SCHEMA = "echomind"

# Everything is generated relative to this instant. 2026-03-31 makes the three invoice
# periods 2026-01/02/03 and the 90-day booking window line up exactly.
REFERENCE = datetime(2026, 3, 31, 17, 0, tzinfo=UTC)
WINDOW_DAYS = 90
PERIODS = ["2026-01", "2026-02", "2026-03"]

RNG_SEED = 1337

# --- Fixed reference data ---------------------------------------------------

# (id, name, code, campus, building, room, address, lat, lon, contact, hours)
# Three sites on two campuses, deliberately: a "nearest core that can do X" question is
# only meaningful when the answer is not always the same building.
FACILITIES = [
    ("fac-imaging", "Advanced Imaging Core", "IMG",
     "North Campus", "Wellcome Building", "Level 2, Rooms 2.10-2.24",
     "14 Rutherford Way, North Campus", 51.524310, -0.133910,
     "imaging-core@example.edu", "08:00-20:00 Mon-Fri"),
    ("fac-genomics", "Genomics Core", "GEN",
     "North Campus", "Crick Wing", "Level 4, Rooms 4.02-4.11",
     "3 Franklin Road, North Campus", 51.526870, -0.129440,
     "genomics-core@example.edu", "08:00-20:00 Mon-Fri"),
    ("fac-massspec", "Mass Spectrometry Core", "MS",
     "Riverside Campus", "Perutz Laboratories", "Basement, Rooms B.01-B.09",
     "88 Sanger Street, Riverside Campus", 51.498220, -0.176500,
     "massspec-core@example.edu", "09:00-18:00 Mon-Fri"),
]

# (id, facility, name, hourly_rate, status, modality, room, techniques, sample_types, spec)
# `techniques` is what a scientist searches on — they ask for "cryo-EM" or "single-cell
# RNA-seq", never for an instrument id they have never seen.
INSTRUMENTS = [
    ("ins-confocal-c2", "fac-imaging", "Confocal C2", 42.00, "available",
     "light microscopy", "2.10",
     ["confocal microscopy", "immunofluorescence", "live-cell imaging", "colocalisation"],
     ["fixed cells", "live cells", "tissue sections"],
     "Point-scanning confocal, 405/488/561/640 nm, 63x oil, resolution ~180 nm lateral."),
    ("ins-confocal-c3", "fac-imaging", "Confocal C3", 46.00, "available",
     "light microscopy", "2.12",
     ["confocal microscopy", "immunofluorescence", "FRAP", "live-cell imaging"],
     ["fixed cells", "live cells", "organoids"],
     "Point-scanning confocal with FRAP module and environmental chamber, 37C and CO2."),
    ("ins-spinning-disk", "fac-imaging", "Spinning Disk SD1", 55.00, "available",
     "light microscopy", "2.14",
     ["spinning disk confocal", "live-cell imaging", "high-speed timelapse"],
     ["live cells", "organoids", "zebrafish embryos"],
     "Spinning disk, sCMOS camera, up to 100 fps, low phototoxicity for long timelapse."),
    ("ins-lightsheet", "fac-imaging", "Light Sheet LS7", 68.00, "maintenance",
     "light microscopy", "2.20",
     ["light sheet microscopy", "whole-mount imaging", "cleared tissue imaging"],
     ["cleared tissue", "embryos", "organoids"],
     "Dual-side light sheet for cleared and whole-mount specimens, up to 1 cm samples."),
    ("ins-em-titan", "fac-imaging", "Cryo-EM Titan", 145.00, "available",
     "electron microscopy", "2.24",
     ["cryo-EM", "single particle analysis", "cryo-electron tomography", "structural biology"],
     ["vitrified grids", "protein complexes", "virus particles"],
     "300 kV cryo-TEM, direct electron detector, single-particle and tomography workflows."),
    ("ins-novaseq", "fac-genomics", "NovaSeq X", 120.00, "available",
     "sequencing", "4.02",
     ["whole genome sequencing", "RNA-seq", "exome sequencing", "high-output sequencing"],
     ["genomic DNA", "total RNA", "libraries"],
     "High-output short-read sequencer, up to 3 Tb per run, 2x150 bp."),
    ("ins-miseq", "fac-genomics", "MiSeq M3", 38.00, "available",
     "sequencing", "4.04",
     ["amplicon sequencing", "16S sequencing", "small genome sequencing", "targeted sequencing"],
     ["amplicons", "bacterial DNA", "libraries"],
     "Benchtop short-read sequencer, up to 15 Gb, 2x300 bp, ideal for amplicons."),
    ("ins-nanopore", "fac-genomics", "Nanopore PromethION", 74.00, "available",
     "sequencing", "4.06",
     ["long-read sequencing", "nanopore sequencing", "de novo assembly", "methylation calling"],
     ["high molecular weight DNA", "native DNA", "RNA"],
     "Long-read nanopore platform, reads over 100 kb, native base modification calling."),
    ("ins-bioanalyzer", "fac-genomics", "Bioanalyzer B4", 22.00, "available",
     "quality control", "4.11",
     ["nucleic acid QC", "library quantification", "RNA integrity"],
     ["total RNA", "libraries", "genomic DNA"],
     "Capillary electrophoresis for sizing and RIN scoring before sequencing."),
    ("ins-orbitrap", "fac-massspec", "Orbitrap Exploris", 96.00, "available",
     "mass spectrometry", "B.01",
     ["proteomics", "LC-MS/MS",
      "post-translational modification analysis", "label-free quantification"],
     ["tryptic digests", "protein extracts", "plasma"],
     "High-resolution Orbitrap LC-MS/MS for deep proteome and PTM analysis."),
    ("ins-qtof", "fac-massspec", "Q-TOF 6546", 61.00, "offline",
     "mass spectrometry", "B.05",
     ["metabolomics", "small molecule identification", "accurate mass", "LC-MS"],
     ["metabolite extracts", "small molecules", "plasma"],
     "Q-TOF for accurate-mass small molecule and metabolomics workflows."),
    ("ins-maldi", "fac-massspec", "MALDI-TOF R2", 44.00, "available",
     "mass spectrometry", "B.09",
     ["MALDI-TOF", "peptide mass fingerprinting", "microbial identification", "intact mass"],
     ["peptides", "intact proteins", "microbial isolates"],
     "MALDI-TOF for rapid intact mass, fingerprinting and organism ID."),
]

LABS = [
    ("lab-a", "Patel Lab (Cell Biology)"),
    ("lab-b", "Ferreira Lab (Microbiology)"),
    ("lab-c", "Okonkwo Lab (Structural Biology)"),
    ("lab-d", "Haruki Lab (Neuroscience)"),
    ("lab-e", "Novak Lab (Immunology)"),
    ("lab-f", "Silva Lab (Plant Sciences)"),
]

ACCOUNT_CODES = [
    ("ACC-A1", "lab-a"), ("ACC-A2", "lab-a"),
    ("ACC-B1", "lab-b"), ("ACC-B2", "lab-b"),
    ("ACC-C1", "lab-c"), ("ACC-D1", "lab-d"),
    ("ACC-E1", "lab-e"), ("ACC-F1", "lab-f"),
]

FILLER_NAMES = [
    "Dana Ruiz", "Evan Cho", "Farah Haddad", "Gus Lindgren", "Hana Kimura",
    "Ivo Petrov", "Jia Chen", "Kofi Mensah", "Lena Brandt", "Mateo Rossi",
    "Nina Berg", "Omar Farouk", "Priya Raman", "Quinn Doyle", "Rosa Iglesias",
    "Sven Aalto", "Tara Byrne", "Umar Sayeed", "Vera Novak", "Wes Larkin",
    "Yara Costa",
]

TEMPLATES = [
    ("tpl-rna-seq", "fac-genomics", "Bulk RNA-seq submission"),
    ("tpl-scrna", "fac-genomics", "Single-cell RNA-seq submission"),
    ("tpl-wgs", "fac-genomics", "Whole genome sequencing"),
    ("tpl-histology", "fac-imaging", "Histology sectioning & staining"),
    ("tpl-live-imaging", "fac-imaging", "Live-cell imaging session"),
    ("tpl-cryo-prep", "fac-imaging", "Cryo-EM grid preparation"),
    ("tpl-proteomics", "fac-massspec", "Proteomics sample analysis"),
    ("tpl-metabolomics", "fac-massspec", "Targeted metabolomics panel"),
]

TEMPLATE_FIELDS = {
    "tpl-rna-seq": [
        {"name": "sample_count", "label": "Number of samples", "type": "integer", "required": True},
        {"name": "organism", "label": "Organism", "type": "string", "required": True},
        {"name": "read_length", "label": "Read length", "type": "enum",
         "options": ["50bp", "100bp", "150bp"], "required": True},
        {"name": "notes", "label": "Notes", "type": "text", "required": False},
    ],
    "tpl-scrna": [
        {"name": "sample_count", "label": "Number of samples", "type": "integer", "required": True},
        {"name": "cells_per_sample", "label": "Target cells per sample",
         "type": "integer", "required": True},
        {"name": "tissue", "label": "Tissue", "type": "string", "required": True},
    ],
    "tpl-wgs": [
        {"name": "sample_count", "label": "Number of samples", "type": "integer", "required": True},
        {"name": "coverage", "label": "Target coverage", "type": "enum",
         "options": ["10x", "30x", "60x"], "required": True},
    ],
    "tpl-histology": [
        {"name": "block_count", "label": "Number of blocks", "type": "integer", "required": True},
        {"name": "stain", "label": "Stain", "type": "enum",
         "options": ["H&E", "Masson", "IHC"], "required": True},
    ],
    "tpl-live-imaging": [
        {"name": "duration_hours", "label": "Session length (hours)",
         "type": "number", "required": True},
        {"name": "instrument", "label": "Preferred instrument",
         "type": "string", "required": False},
        {"name": "co2", "label": "CO2 incubation required", "type": "boolean", "required": True},
    ],
    "tpl-cryo-prep": [
        {"name": "grid_count", "label": "Number of grids", "type": "integer", "required": True},
        {"name": "buffer", "label": "Buffer", "type": "string", "required": True},
    ],
    "tpl-proteomics": [
        {"name": "sample_count", "label": "Number of samples", "type": "integer", "required": True},
        {"name": "digest", "label": "Digest protocol", "type": "enum",
         "options": ["trypsin", "chymotrypsin", "LysC"], "required": True},
    ],
    "tpl-metabolomics": [
        {"name": "sample_count", "label": "Number of samples", "type": "integer", "required": True},
        {"name": "panel", "label": "Panel", "type": "enum",
         "options": ["central-carbon", "lipids", "amino-acids"], "required": True},
    ],
}

PROJECTS = [
    ("prj-neuro-atlas", "Cortical Cell Atlas"),
    ("prj-biofilm", "Biofilm Resistance Survey"),
    ("prj-crystal", "Membrane Protein Crystallography"),
    ("prj-metabolic", "Metabolic Flux in Hypoxia"),
]

SAMPLE_STATES = ["received", "in_prep", "on_instrument", "qc", "delivered"]
REQUEST_STATUSES = ["submitted", "in_progress", "completed", "rejected"]
BOOKING_STATUSES = ["requested", "confirmed", "cancelled", "completed"]

MAINTENANCE_NOTES = {
    "preventive": ["Quarterly service", "Laser alignment check", "Objective cleaning",
                   "Calibration with reference standard", "Firmware update"],
    "repair": ["Replaced 488nm laser module", "Stage motor replacement", "Detector board swap",
               "Fixed coolant leak", "Replaced vacuum pump"],
    "alert": ["Temperature excursion logged", "Vibration threshold exceeded",
              "Humidity out of range", "Unexpected shutdown", "Pressure sensor warning"],
}

# The demo's precise, verifiable billing answer: Lab A, March 2026, Confocal C2 = $412.00
STORY_LINES = [
    ("Confocal C2 imaging time — March week 2", "ins-confocal-c2", 6.00, 42.00, 252.00),
    ("Confocal C2 imaging time — March week 4", "ins-confocal-c2", 4.00, 40.00, 160.00),
]
STORY_ACCOUNT = "ACC-A1"
STORY_PERIOD = "2026-03"
STORY_TOTAL = 412.00


def apply_migrations(conn) -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        print(f"  applying {path.name}")
        # Through the raw DBAPI cursor, not exec_driver_sql. SQLAlchemy passes an empty
        # parameter tuple, which makes psycopg parse the statement for placeholders — so a
        # migration containing a literal % ("charged at 50% of the booked time") fails with
        # "incomplete placeholder". Migrations are static DDL and never take parameters;
        # doubling the %% instead would corrupt the text that reaches the database and
        # break `psql -f` on the same file.
        cursor = conn.connection.cursor()
        try:
            cursor.execute(path.read_text())
        finally:
            cursor.close()


def setup_checkpointer() -> None:
    """Create LangGraph's checkpoint tables, as the owner.

    The running API connects as echomind_app, which has no DDL — so it cannot create
    these itself, and its own setup() call is expected to be a no-op. Migration 003 sets
    default privileges in `public` for the owner, so the tables the saver creates here
    are readable and writable by the app without a second grant step.
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    conn_string = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    # Pin the schema. The saver issues unqualified CREATE TABLE, so without this the
    # tables follow `"$user"` and land in a schema named after whoever ran the migration.
    # %3D is the '=' inside the libpq options parameter.
    separator = "&" if "?" in conn_string else "?"
    conn_string += f"{separator}options=-csearch_path%3D{CHECKPOINT_SCHEMA}"

    with PostgresSaver.from_conn_string(conn_string) as saver:
        saver.setup()

    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"""GRANT SELECT, INSERT, UPDATE, DELETE
               ON {CHECKPOINT_SCHEMA}.checkpoints, {CHECKPOINT_SCHEMA}.checkpoint_writes,
                  {CHECKPOINT_SCHEMA}.checkpoint_blobs, {CHECKPOINT_SCHEMA}.checkpoint_migrations
               TO echomind_app"""
        )
    print(f"  checkpointer tables ready in schema {CHECKPOINT_SCHEMA}")


def truncate(conn) -> None:
    conn.exec_driver_sql(
        """
        TRUNCATE infinity.invoice_lines, infinity.invoices, infinity.account_codes,
                 infinity.maintenance_events, infinity.samples, infinity.service_requests,
                 infinity.request_templates, infinity.project_members, infinity.projects,
                 infinity.usage_records, infinity.bookings, infinity.instruments,
                 infinity.facilities, infinity.users, infinity.labs
        RESTART IDENTITY CASCADE
        """
    )


def build_users(rng: random.Random) -> list[dict]:
    users: list[dict] = []
    for handle, u in DEMO_USERS.items():
        users.append({
            "id": u["id"],
            "email": u["email"],
            "name": u["name"],
            "role": u["role"],
            "lab_id": u["lab_id"],
            "training": json.dumps(
                {"confocal": True, "biosafety-2": True, "cryo-em": handle in ("asha", "cora")}
            ),
            "account_codes": list(u["account_codes"]),
        })

    lab_ids = [lab_id for lab_id, _ in LABS]
    for i, name in enumerate(FILLER_NAMES):
        lab_id = lab_ids[i % len(lab_ids)]
        suffix = lab_id.rsplit("-", 1)[1].upper()
        codes = [c for c, lid in ACCOUNT_CODES if lid == lab_id]
        users.append({
            "id": f"u-{name.split()[0].lower()}",
            "email": f"{name.split()[0].lower()}@example.edu",
            "name": name,
            "role": "user",
            "lab_id": lab_id,
            "training": json.dumps({
                "confocal": rng.random() < 0.7,
                "biosafety-2": rng.random() < 0.8,
                "cryo-em": rng.random() < 0.2,
            }),
            "account_codes": [rng.choice(codes)] if codes else [f"ACC-{suffix}1"],
        })
    return users


def seed() -> None:
    rng = random.Random(RNG_SEED)

    with engine.begin() as conn:
        print("migrations:")
        apply_migrations(conn)
        print("seeding:")
        truncate(conn)

        # --- labs, users, facilities, instruments, account codes ---
        conn.execute(
            text("INSERT INTO infinity.labs (id, name) VALUES (:id, :name)"),
            [{"id": i, "name": n} for i, n in LABS],
        )
        users = build_users(rng)
        conn.execute(
            text(
                """INSERT INTO infinity.users
                   (id, email, name, role, lab_id, training, account_codes)
                   VALUES (:id, :email, :name, :role, :lab_id,
                           CAST(:training AS jsonb), :account_codes)"""
            ),
            users,
        )
        # asha is PI of lab A; give the other labs a PI from their own members.
        conn.execute(
            text("UPDATE infinity.labs SET pi_user_id = :pi WHERE id = 'lab-a'"),
            {"pi": DEMO_USERS["asha"]["id"]},
        )
        for lab_id, _ in LABS[1:]:
            member = next(u for u in users if u["lab_id"] == lab_id)
            conn.execute(
                text("UPDATE infinity.labs SET pi_user_id = :pi WHERE id = :lab"),
                {"pi": member["id"], "lab": lab_id},
            )

        conn.execute(
            text("""INSERT INTO infinity.facilities
                        (id, name, code, campus, building, room, address,
                         latitude, longitude, contact_email, opening_hours)
                     VALUES (:id, :name, :code, :campus, :building, :room, :address,
                             :lat, :lon, :contact, :hours)"""),
            [{"id": i, "name": n, "code": c, "campus": ca, "building": b, "room": rm,
              "address": ad, "lat": la, "lon": lo, "contact": ct, "hours": hr}
             for i, n, c, ca, b, rm, ad, la, lo, ct, hr in FACILITIES],
        )
        conn.execute(
            text(
                """INSERT INTO infinity.instruments
                       (id, facility_id, name, hourly_rate, status,
                        modality, techniques, sample_types, specification, room)
                   VALUES (:id, :fac, :name, :rate, :status,
                           :mod, :tech, :samples, :spec, :room)"""
            ),
            [{"id": i, "fac": f, "name": n, "rate": r, "status": st,
              "mod": mo, "room": ro, "tech": te, "samples": sa, "spec": sp}
             for i, f, n, r, st, mo, ro, te, sa, sp in INSTRUMENTS],
        )
        conn.execute(
            text("INSERT INTO infinity.account_codes (code, lab_id) VALUES (:code, :lab)"),
            [{"code": c, "lab": lid} for c, lid in ACCOUNT_CODES],
        )

        user_ids = [u["id"] for u in users]
        user_by_id = {u["id"]: u for u in users}
        instrument_ids = [i[0] for i in INSTRUMENTS]

        # --- 200 bookings over the 90 days before the reference date ---
        # The four demo users get a guaranteed share so every demo scene has data.
        bookings = []
        window_start = REFERENCE - timedelta(days=WINDOW_DAYS)
        guaranteed = (
            [DEMO_USERS["alice"]["id"]] * 14
            + [DEMO_USERS["bob"]["id"]] * 10
            + [DEMO_USERS["asha"]["id"]] * 8
            + [DEMO_USERS["cora"]["id"]] * 3
        )
        for n in range(200):
            uid = guaranteed[n] if n < len(guaranteed) else rng.choice(user_ids)
            iid = rng.choice(instrument_ids)
            offset_min = rng.randrange(0, WINDOW_DAYS * 24 * 60, 30)
            starts = window_start + timedelta(minutes=offset_min)
            duration = rng.choice([1, 1.5, 2, 3, 4, 6, 8])
            ends = starts + timedelta(hours=duration)
            codes = user_by_id[uid]["account_codes"]
            bookings.append({
                "id": f"bk-{n:04d}",
                "user_id": uid,
                "instrument_id": iid,
                "starts_at": starts,
                "ends_at": ends,
                "status": ("completed" if ends < REFERENCE - timedelta(days=1)
                           else rng.choice(BOOKING_STATUSES)),
                "account_code": codes[0] if codes else None,
            })
        conn.execute(
            text(
                """INSERT INTO infinity.bookings
                   (id, user_id, instrument_id, starts_at, ends_at, status, account_code)
                   VALUES (:id, :user_id, :instrument_id, :starts_at, :ends_at,
                           :status, :account_code)"""
            ),
            bookings,
        )

        # --- 500 usage records: 320 scheduled (tied to a booking), 180 tracked
        #     (120 of those with no booking at all — walk-up usage) ---
        usage = []
        active_bookings = [b for b in bookings if b["status"] in ("confirmed", "completed")]
        for n in range(320):
            b = active_bookings[n % len(active_bookings)]
            usage.append({
                "id": f"ur-{n:04d}",
                "instrument_id": b["instrument_id"],
                "user_id": b["user_id"],
                "booking_id": b["id"],
                "starts_at": b["starts_at"],
                "ends_at": b["ends_at"],
                "source": "scheduled",
            })
        for n in range(320, 500):
            attached = n < 380  # 60 tracked records reconcile to a booking, 120 do not
            if attached:
                b = active_bookings[(n * 7) % len(active_bookings)]
                starts = b["starts_at"] + timedelta(minutes=rng.randrange(0, 30))
                usage.append({
                    "id": f"ur-{n:04d}",
                    "instrument_id": b["instrument_id"],
                    "user_id": b["user_id"],
                    "booking_id": b["id"],
                    "starts_at": starts,
                    "ends_at": starts + timedelta(minutes=rng.randrange(30, 240)),
                    "source": "tracked",
                })
            else:
                starts = window_start + timedelta(
                    minutes=rng.randrange(0, WINDOW_DAYS * 24 * 60, 15))
                usage.append({
                    "id": f"ur-{n:04d}",
                    "instrument_id": rng.choice(instrument_ids),
                    "user_id": rng.choice(user_ids),
                    "booking_id": None,
                    "starts_at": starts,
                    "ends_at": starts + timedelta(minutes=rng.randrange(20, 300)),
                    "source": "tracked",
                })
        conn.execute(
            text(
                """INSERT INTO infinity.usage_records
                   (id, instrument_id, user_id, booking_id, starts_at, ends_at, source)
                   VALUES (:id, :instrument_id, :user_id, :booking_id, :starts_at,
                           :ends_at, :source)"""
            ),
            usage,
        )

        # --- 8 templates, 40 service requests, samples ---
        conn.execute(
            text(
                """INSERT INTO infinity.request_templates (id, facility_id, name, fields)
                   VALUES (:id, :fac, :name, CAST(:fields AS jsonb))"""
            ),
            [{"id": i, "fac": f, "name": n, "fields": json.dumps(TEMPLATE_FIELDS[i])}
             for i, f, n in TEMPLATES],
        )

        requests, samples = [], []
        sample_n = 0
        for n in range(40):
            uid = (DEMO_USERS["alice"]["id"] if n < 6
                   else DEMO_USERS["bob"]["id"] if n < 10
                   else rng.choice(user_ids))
            tpl_id, _, _tpl_name = TEMPLATES[n % len(TEMPLATES)]
            status = REQUEST_STATUSES[n % len(REQUEST_STATUSES)]
            created = REFERENCE - timedelta(days=rng.randrange(1, WINDOW_DAYS))
            fields = {"sample_count": rng.randrange(2, 12), "organism": "Mus musculus"}
            history = [{"at": created.isoformat(), "status": "submitted", "by": uid}]
            if status != "submitted":
                history.append({
                    "at": (created + timedelta(days=1)).isoformat(),
                    "status": status,
                    "by": DEMO_USERS["cora"]["id"],
                })
            requests.append({
                "id": f"sr-{n:03d}",
                "user_id": uid,
                "template_id": tpl_id,
                "fields": json.dumps(fields),
                "status": status,
                "history": json.dumps(history),
            })
            for _ in range(rng.randrange(1, 6)):
                samples.append({
                    "id": f"sm-{sample_n:04d}",
                    "request_id": f"sr-{n:03d}",
                    "barcode": f"BC{100000 + sample_n}",
                    "state": rng.choice(SAMPLE_STATES),
                    "updated_at": created + timedelta(days=rng.randrange(0, 5)),
                })
                sample_n += 1
        conn.execute(
            text(
                """INSERT INTO infinity.service_requests
                   (id, user_id, template_id, fields, status, history)
                   VALUES (:id, :user_id, :template_id, CAST(:fields AS jsonb),
                           :status, CAST(:history AS jsonb))"""
            ),
            requests,
        )
        conn.execute(
            text(
                """INSERT INTO infinity.samples (id, request_id, barcode, state, updated_at)
                   VALUES (:id, :request_id, :barcode, :state, :updated_at)"""
            ),
            samples,
        )

        # --- 4 projects with members ---
        conn.execute(
            text("INSERT INTO infinity.projects (id, name, currency) VALUES (:id, :name, 'USD')"),
            [{"id": i, "name": n} for i, n in PROJECTS],
        )
        members = []
        for idx, (pid, _) in enumerate(PROJECTS):
            member_ids = {DEMO_USERS["asha"]["id"] if idx == 0 else user_ids[idx]}
            member_ids.update(rng.sample(user_ids, 4))
            for k, uid in enumerate(sorted(member_ids)):
                members.append({"pid": pid, "uid": uid, "role": "lead" if k == 0 else "member"})
        conn.execute(
            text(
                """INSERT INTO infinity.project_members (project_id, user_id, role)
                   VALUES (:pid, :uid, :role)"""
            ),
            members,
        )

        # --- invoices: 3 periods x 8 account codes, lines summing to totals ---
        # Confocal C2 is excluded from random lines so the March story is unambiguous:
        # every Confocal C2 line in the dataset is placed explicitly below.
        billable = [(i, n, r) for i, _f, n, r, *_rest in INSTRUMENTS if i != "ins-confocal-c2"]
        invoices, lines = [], []
        line_n = 0
        for period in PERIODS:
            for code, _lab in ACCOUNT_CODES:
                inv_id = f"inv-{code}-{period}"
                invoices.append({"id": inv_id, "code": code, "period": period})
                for _ in range(rng.randrange(2, 6)):
                    iid, iname, rate = rng.choice(billable)
                    qty = round(rng.randrange(2, 40) / 2, 2)
                    amount = round(qty * rate, 2)
                    lines.append({
                        "id": f"il-{line_n:05d}", "inv": inv_id,
                        "desc": f"{iname} usage — {period}",
                        "iid": iid, "qty": qty, "unit": rate, "amount": amount,
                    })
                    line_n += 1

        def add_line(inv_id, desc, iid, qty, unit, amount):
            nonlocal line_n
            lines.append({"id": f"il-{line_n:05d}", "inv": inv_id, "desc": desc,
                          "iid": iid, "qty": qty, "unit": unit, "amount": amount})
            line_n += 1

        # The story itself: Lab A / ACC-A1 / 2026-03 / Confocal C2 == exactly $412.00
        for desc, iid, qty, unit, amount in STORY_LINES:
            add_line(f"inv-{STORY_ACCOUNT}-{STORY_PERIOD}", desc, iid, qty, unit, amount)
        # Confocal C2 elsewhere, for realism — never Lab A in March.
        add_line("inv-ACC-A1-2026-01", "Confocal C2 imaging time — January", "ins-confocal-c2",
                 5.00, 42.00, 210.00)
        add_line("inv-ACC-A2-2026-02", "Confocal C2 imaging time — February", "ins-confocal-c2",
                 3.00, 42.00, 126.00)
        add_line("inv-ACC-B1-2026-03", "Confocal C2 imaging time — March", "ins-confocal-c2",
                 7.00, 42.00, 294.00)

        totals: dict[str, float] = {}
        for ln in lines:
            totals[ln["inv"]] = round(totals.get(ln["inv"], 0.0) + float(ln["amount"]), 2)

        conn.execute(
            text(
                """INSERT INTO infinity.invoices (id, account_code, period, total)
                   VALUES (:id, :code, :period, :total)"""
            ),
            [{**inv, "total": totals.get(inv["id"], 0.0)} for inv in invoices],
        )
        conn.execute(
            text(
                """INSERT INTO infinity.invoice_lines
                   (id, invoice_id, description, instrument_id, qty, unit_price, amount)
                   VALUES (:id, :inv, :desc, :iid, :qty, :unit, :amount)"""
            ),
            lines,
        )

        # --- 60 maintenance events ---
        events = []
        for n in range(60):
            kind = ["preventive", "repair", "alert"][n % 3]
            occurred = window_start + timedelta(minutes=rng.randrange(0, WINDOW_DAYS * 24 * 60))
            downtime = {"preventive": rng.randrange(1, 5), "repair": rng.randrange(4, 48),
                        "alert": 0}[kind]
            events.append({
                "id": f"me-{n:03d}",
                "iid": rng.choice(instrument_ids),
                "kind": kind,
                "notes": rng.choice(MAINTENANCE_NOTES[kind]),
                "occurred_at": occurred,
                "downtime": float(downtime),
            })
        conn.execute(
            text(
                """INSERT INTO infinity.maintenance_events
                   (id, instrument_id, kind, notes, occurred_at, downtime_hours)
                   VALUES (:id, :iid, :kind, :notes, :occurred_at, :downtime)"""
            ),
            events,
        )

    setup_checkpointer()
    verify()


def verify() -> None:
    """Print the counts and assert the demo story survived."""
    checks = {
        "facilities": "SELECT count(*) FROM infinity.facilities",
        "instruments": "SELECT count(*) FROM infinity.instruments",
        "labs": "SELECT count(*) FROM infinity.labs",
        "users": "SELECT count(*) FROM infinity.users",
        "bookings": "SELECT count(*) FROM infinity.bookings",
        "usage_records": "SELECT count(*) FROM infinity.usage_records",
        "request_templates": "SELECT count(*) FROM infinity.request_templates",
        "service_requests": "SELECT count(*) FROM infinity.service_requests",
        "samples": "SELECT count(*) FROM infinity.samples",
        "projects": "SELECT count(*) FROM infinity.projects",
        "invoice_periods": "SELECT count(DISTINCT period) FROM infinity.invoices",
        "invoice_lines": "SELECT count(*) FROM infinity.invoice_lines",
        "maintenance_events": "SELECT count(*) FROM infinity.maintenance_events",
    }
    with engine.connect() as conn:
        for label, sql in checks.items():
            print(f"  {label:<20} {conn.execute(text(sql)).scalar_one()}")

        mismatched = conn.execute(
            text(
                """SELECT count(*) FROM infinity.invoices i
                   WHERE i.total <> (SELECT COALESCE(sum(amount), 0)
                                     FROM infinity.invoice_lines WHERE invoice_id = i.id)"""
            )
        ).scalar_one()
        story = conn.execute(
            text(
                """SELECT COALESCE(sum(amount), 0) FROM reporting.v_billing_lines
                   WHERE lab_id = 'lab-a' AND period = '2026-03' AND instrument = 'Confocal C2'"""
            )
        ).scalar_one()

    print(f"  {'invoice total drift':<20} {mismatched}")
    print(f"  {'lab-a Mar Confocal C2':<20} ${story}")
    assert mismatched == 0, "invoice totals do not match their lines"
    assert float(story) == STORY_TOTAL, f"demo story broken: expected 412.00, got {story}"
    print("seed OK")


if __name__ == "__main__":
    seed()
