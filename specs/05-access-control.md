# Spec 05 — Access control

## Identity

JWT (HS256, JWT_SECRET) claims: {sub: user_id, role: 'user'|'pi'|'admin',
lab_ids: [..], facility_ids: [..], name}. scripts/mint_jwt.py prints tokens for the
four demo users. FastAPI dependency verifies the token and builds ctx; the MCP server
and every API route require it. No anonymous access.

## Tier matrix (enforced in tool handlers, tested)

- T0: tools 2, 3, 10(status) — any authenticated user.
- T1: tools 1(self), 4, 5(user scope), 6(mine), 7(own lab), 8(own account codes),
  12, 13, 14, 15(user templates) — caller's own data only.
- T2 (pi): everything T1 plus lab-scoped reads (1, 5, 6, 7, 8, 9) for their lab_ids,
  and run_readonly_sql restricted to their labs via SQL rewrite.
- T3 (admin): all reads unrestricted, 10(history), unrestricted run_readonly_sql,
  approve any action, generate_document admin templates.

## Three enforcement points

1. Tool layer: tier check before any query; 'forbidden' errors reveal nothing.
2. Database: run_readonly_sql uses echomind_readonly (SELECT on four views only);
   pi queries get lab_id predicates injected server-side.
3. Retrieval: the single permission filter from spec 03.

The LLM is never an enforcement point. No prompt text may grant or deny access.

## Required tests (pytest -m tiers)

- bob calling get_user_profile(alice) -> forbidden; response identical in shape to a
  nonexistent user id (no existence leak).
- asha reads lab-A usage but is forbidden lab-B; her SQL for v_billing_lines returns
  only lab-A rows even when she writes no lab filter.
- alice cannot approve bob's pending action; cora can.
- A request with a tampered JWT signature is rejected at the dependency.
- Chat-level: bob asks "show me alice's bookings" -> the agent's tool call fails tier
  check and the reply is the forbidden explanation, not data.
