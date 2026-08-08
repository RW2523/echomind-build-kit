"""Print HS256 JWTs for the four demo users.

    python -m scripts.mint_jwt            # all four, table form
    python -m scripts.mint_jwt alice      # just the token, for $(...) use
    python -m scripts.mint_jwt --json     # {handle: token}
"""

from __future__ import annotations

import json
import sys

from server.auth import mint
from server.demo_identities import DEMO_USERS


def token_for(handle: str) -> str:
    u = DEMO_USERS[handle]
    return mint(
        user_id=u["id"],
        name=u["name"],
        role=u["role"],
        lab_ids=u["lab_ids"],
        facility_ids=u["facility_ids"],
    )


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("-")]
    as_json = "--json" in argv

    if args:
        handle = args[0].lower()
        if handle not in DEMO_USERS:
            print(f"unknown user {handle!r}; choose from {', '.join(DEMO_USERS)}", file=sys.stderr)
            return 2
        print(token_for(handle))
        return 0

    tokens = {h: token_for(h) for h in DEMO_USERS}
    if as_json:
        print(json.dumps(tokens, indent=2))
        return 0

    for handle, tok in tokens.items():
        u = DEMO_USERS[handle]
        print(f"\n{handle}  ({u['role']}) — {u['blurb']}")
        print(f"  id={u['id']}  labs={u['lab_ids']}")
        print(f"  {tok}")
    print("\nUse:  curl -H \"Authorization: Bearer $(python -m scripts.mint_jwt alice)\" ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
