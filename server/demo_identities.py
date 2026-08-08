"""The four fixed demo identities (spec 01).

Single source of truth shared by the seeder, scripts/mint_jwt.py, the tests, the demo
runner and the UI's login-as switcher — so an id can never drift between them.
"""

from __future__ import annotations

from typing import TypedDict

LAB_A = "lab-a"
LAB_B = "lab-b"

FACILITY_IDS = ["fac-imaging", "fac-genomics", "fac-massspec"]


class DemoUser(TypedDict):
    id: str
    name: str
    email: str
    role: str
    lab_id: str | None
    lab_ids: list[str]
    facility_ids: list[str]
    account_codes: list[str]
    blurb: str


DEMO_USERS: dict[str, DemoUser] = {
    "alice": {
        "id": "u-alice",
        "name": "Alice Nguyen",
        "email": "alice@example.edu",
        "role": "user",
        "lab_id": LAB_A,
        "lab_ids": [LAB_A],
        "facility_ids": [],
        "account_codes": ["ACC-A1"],
        "blurb": "Researcher, Lab A — sees only her own data",
    },
    "bob": {
        "id": "u-bob",
        "name": "Bob Okafor",
        "email": "bob@example.edu",
        "role": "user",
        "lab_id": LAB_B,
        "lab_ids": [LAB_B],
        "facility_ids": [],
        "account_codes": ["ACC-B1"],
        "blurb": "Researcher, Lab B — must never see Lab A data",
    },
    "asha": {
        "id": "u-asha",
        "name": "Asha Patel",
        "email": "asha@example.edu",
        "role": "pi",
        "lab_id": LAB_A,
        "lab_ids": [LAB_A],
        "facility_ids": [],
        "account_codes": ["ACC-A1", "ACC-A2"],
        "blurb": "PI of Lab A — lab-scoped reads, Lab A only",
    },
    "cora": {
        "id": "u-cora",
        "name": "Cora Lindqvist",
        "email": "cora@example.edu",
        "role": "admin",
        "lab_id": None,
        "lab_ids": [LAB_A, LAB_B, "lab-c", "lab-d", "lab-e", "lab-f"],
        "facility_ids": FACILITY_IDS,
        "account_codes": [],
        "blurb": "Core facility admin — all facilities, can approve any action",
    },
}

# id -> handle, for reverse lookups in tests and the UI.
BY_ID = {u["id"]: handle for handle, u in DEMO_USERS.items()}
