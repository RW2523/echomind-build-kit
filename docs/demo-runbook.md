# Demo runbook

`specs/08-demo.md` says what the six scenes prove. This says how to run them in front of
people without anything going wrong, and what to do when something does.

## Before the room (T-30 minutes)

```bash
make up                                  # postgres
docker compose --profile rerank up -d    # reranker
make seed                                # seed + embed the corpus
make api                                 # in its own terminal
make ui                                  # in another
```

Then the check that actually matters — run the whole demo once, headless:

```bash
make demo
```

Six PASS lines and nothing else. If any scene fails you have 30 minutes to fix it, which
is the entire point of running it now rather than discovering it live. `make demo` cleans
up after itself, so the seed is restored and you can run it again.

Then the one that catches what the scripted scenes cannot:

```bash
make convo
```

Whole conversations on a single thread, asserting that turn N does not contradict turn
N-1. `make demo` and `make eval` were both green while a live three-turn conversation
called an instrument available one turn after refusing to book it for being under
maintenance — every scripted scene is one turn deep, and that class of bug only exists
between turns. Every proposal it makes is declined, so it leaves the seed untouched and
you can run it as often as you like. It starts its own API if none is running and leaves
one it did not start alone, so it works from a cold shell — and it runs nightly in CI on
the GPU runner.

**If you drive the UI by hand before the demo, run this rather than trusting that
yesterday's run still holds.**

Confirm the model is the one you mean to show:

```bash
curl -s localhost:8080/readyz | python3 -m json.tool
```

`llm_model` should read `nvidia/Qwen3-8B-FP4` and `escalation_enabled` should be `false`
— someone always asks whether it phones home, and the answer is on screen.

## The record-first rule

**Record the full demo before the meeting and have the file open in a background tab.**

Not because the system is unreliable — `make demo` passing 30 minutes earlier is good
evidence — but because the failure modes left are environmental: the GPU busy from
someone else's job, a laptop that sleeps, a projector that drops HDMI while a 9-second
answer is streaming. A recording turns a dead demo into a five-second recovery: "the box
is busy, here's the same run from this morning", and you keep the room.

Record at 1400×900 or wider. Below about 820px the UI switches to its phone layout, which
is correct behaviour and looks like a bug on a projector.

## Running the six scenes

Scene order is deliberate — each one sets up the next. From `specs/08-demo.md`:

1. **Onboarding** — nothing happens without approval. Establishes the approval card
   before anyone has a reason to distrust it.
2. **Availability, then a booking that waits for you** — the same pattern, now with a
   write people care about.
3. **Billing truth** — "why was my lab charged $412 in March?" Expand the evidence table.
   This is the moment to say the number came from the ledger, not the model.
4. **A filled form becomes a service request** — upload, then a pre-filled proposal.
5. **Per-user knowledge** — sign in as alice, ask about her upload, get it. Sign in as
   bob, ask the identical question, get a refusal. Do this switch **live**; it is the
   single most persuasive thing in the demo and it takes eleven seconds.
6. **Verified or silent** — ask the parking-permit question deliberately. The honest
   redirect is the product. Then open the admin console: audit trail, eval scores, and
   `/admin/gaps` showing that the question you just failed to answer is now top of the
   list of documents to write.

**Do not skip scene 6 for time.** Cut scene 3 instead. A demo that only ever succeeds is
the demo every vendor gives; the one that refuses on purpose is the one they remember.

## When something goes wrong

| What you see | What it is | What to do |
|---|---|---|
| Answer takes >20s | GPU busy with another job | Say so, switch to the recording |
| "I could not complete that lookup" | Backend hiccup, already logged | Ask it again; it is not a guess, which is the point |
| A redirect you did not plan | Corpus genuinely lacks it | Lean in: show `/admin/gaps`, it just proved the feature |
| UI shows the phone layout | Window under 820px | Widen the window |
| `/demo/login` returns 404 | `JWT_SECRET` is set but `DEMO_LOGIN_ENABLED` is not | Set `DEMO_LOGIN_ENABLED=true` and restart the API |
| Approval fails with a privilege error | Migrations not fully applied | `make seed` — 005 grants the app role its three inserts |
| "must fall within opening hours" | Slot outside 08:00–20:00 UTC | Correct, not a fault — pick a slot inside the window |
| A booking proposes the wrong instrument | Should not happen; `carry_forward_instrument` pins it to the conversation | Decline it, say so on the spot, and file it — the approval card is exactly why this is survivable |

## Questions you will be asked

- **"Does it call OpenAI?"** No. `escalation_enabled: false` on `/readyz`, and the
  escalation lane is a stub behind that flag. Everything runs on the box in the room.
- **"What if it's wrong?"** Show scene 6, then the faithfulness score on the admin
  console. The claim is not that it is never wrong; it is that it is not *confidently*
  wrong — every claim is checked against its cited source before it ships.
- **"Can it see my data?"** Scene 5, live.
- **"How do you know it stays accurate?"** `make eval` on a 20-item golden set, and CI
  blocks a release if the data exact-match or the refusal rate drops below 100%.
- **"What happens when we change a prompt?"** `/admin/prompts` — every prompt carries a
  version derived from its own text, and every trace records which versions ran.

## After

```bash
make demo          # confirms the box is back to a known state
```

The demo and the checklist both clean up the rows they create. If you drove the UI by
hand and approved anything, that booking is still there — `make eval` asserts alice has
exactly 20 bookings and will fail until it is removed.
