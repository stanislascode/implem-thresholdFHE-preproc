# modconv1-preproc: real secure preprocessing for ModConv1 (beta=2)

Implements `Pi_ModConv1Preproc^{q,t,Q}` (paper Section 4.1, "Secure
preprocessing for `Pi_ModConv1^{q,t,2}`"), replacing
`modconv1_dealer.py`'s trusted-dealer stand-in with a real MPC protocol:
no party ever learns the mask `m`. Built directly on `xorbits.mpc` /
`multibits_small.mpc` / `multibits_large.mpc` (MultiBit generation +
consistency check) plus a new implementation of `Pi_Compare`
(`compare.tex`).

## Files

- `Programs/Source/modconv1_preproc_q.mpc` — q-domain: generates
  `k * kappa_retries` MultiBit candidate bits (`k = ceil(log2 q)`) via
  `xorBits(q)` + the MultiBitsConsCheck "side i" opening, then persists
  each candidate's `(m_L, m_U)` pair, secret, via MP-SPDZ's Persistence
  mechanism.
- `Programs/Source/modconv1_preproc_t.mpc` — t-domain: same generation,
  plus the real `Pi_Compare` protocol and the accept-bit computation for
  every candidate (see "Why Q := t" below).
- `Programs/Source/modconv1_preproc_check.mpc` — the auxiliary large
  modulus's side of the consistency check; identical to
  `multibits_large.mpc`.
- `Programs/Source/modconv1_preproc_finalize_{q,t}.mpc` — once the
  accepted candidate index is known, read it back from Persistence and
  compute the final `m` (q-domain) / `u_0, u_1` (t-domain), persisted for
  the online phase.
- `Programs/Source/modconv1_preproc_verify_{q,t}.mpc` — **test only**:
  reveal the final `m`, `u_0`, `u_1` so the orchestrator can cross-check
  them against a plaintext computation. Never run against a real
  deployment's output.
- `Scripts/modconv1_preproc_gen.py` — orchestrates all of the above.
- This file.

## Why Q := t

Step 1 of the paper's protocol calls `func_Abb.MultiBit(Q, q, t)` — a bit
shared over three moduli, where `Q` is an auxiliary modulus used only for
the `Compare` step and can be small (the paper explicitly suggests
`Q = 2`, or, cheaper still: *"Taking Q to be q or t, whichever multiplies
more cheaply, costs nothing at all, the MultiBits then being over two
moduli rather than three."* This implementation takes that option, with
`Q := t`: `Compare` and the accept bit are computed directly on the same
bits already shared over `t` from `xorBits(t)`, so there's no third live
domain to manage. (There's still a large *auxiliary* modulus involved --
`modconv1_preproc_check.mpc`'s `Qcheck` -- but that one is for
`MultiBitsConsCheck` itself, exactly as in `multibits_README.md`, and is
unrelated to the paper's `Q`.)

## What's real vs. simplified here

Real:
- MultiBit generation and its consistency check: unchanged from
  `multibits_small.mpc`/`multibits_large.mpc`, see that README for what's
  real there (Input, native secure multiplication, native `RandomBit`,
  coin-tossed challenges, the actual `MultiBitsConsCheck` bookkeeping).
- `Pi_Compare`: the suffix-AND `E_j` is computed by real sequential
  secure multiplications (`E[j] = E[j+1] * e[j]`, MP-SPDZ's native
  `sint` multiplication), exactly as the protocol specifies; `eq`, `lt`
  and the final `accept = lt + eq*(1 - m_U)` are local linear
  combinations plus one more real multiplication (`eq * m_U`).
- The mask `m` and corrections `u_0`, `u_1`: never opened. They're
  written to and read from MP-SPDZ's Persistence
  (`Persistence/Transactions-P<i>.data`) as genuine secret shares, the
  same mechanism a real multi-run deployment would use to carry state
  from preprocessing into the online phase.
- The accept/reject transcript is revealed, exactly as the paper's own
  security proof does (`thm:modconv1-preproc`'s simulator "samples r from
  [a] geometric distribution ... and shows the corresponding bits" --
  revealing which iteration was accepted is provably safe, unlike
  revealing `m` itself).

Simplified, and documented as such:
- **Batched retries instead of early-stopping.** The paper's protocol
  generates one candidate, checks it, and *stops as soon as one is
  accepted* -- so a real transcript only ever contains the bits up to and
  including the first accept. This implementation instead generates all
  `kappa_retries` candidates up front in one batch (so all of them can be
  produced by one MultiBit-generation run rather than looping the whole
  three-domain pipeline once per attempt) and opens every accept bit,
  not just the ones up to the first success. This reveals up to
  `kappa_retries - 1` extra fair-coin-like accept/reject bits beyond what
  early-stopping would have shown (the accepted `m` itself remains exactly
  as secret either way, since which candidate is *chosen* still says
  nothing about its value). Purely a batching/engineering simplification,
  not a change to what's computed or to `m`'s secrecy.
- **`CheckedInput`** is the same per-bit `open(x*(1-x))==0` stand-in as in
  `multibits_README.md`, inherited from `xorBits`.

## Verified behaviour

Every run below used `--verify` (test-only reveal) to cross-check the
final `(m, u_0, u_1)` against the same relations `modconv1_dealer.py`
computes in the clear: `m` uniform-looking on `Z_q`, `u_0 = m - q*m_U mod t`,
`u_1 = m mod t`, where `m_U = 1{alpha(m) > 0}`.

| N | q (bits) | t (bits) | kappa_retries | kappa_stat | seeds tried | result |
|---|---|---|---|---|---|---|
| 3 | 43 | 23 | 8 | 16 | 5, 3 more | all PASS, m/u_0/u_1 exact match |
| 5 | 43 | 23 | 8 | 16 | 11 | PASS |
| 3 | 43 | 23 | 1 | 16 | 20-24 | all PASS (candidate accepted) |
| 3 | 43 | 23 | 1 | 16 | 102 | correctly aborts: "All 1 candidates rejected" |

The last row confirms the retry/abort logic actually engages (not just
the accept path): with only one candidate per run, roughly 8% of seeds
reject it (matching `1 - q/2^k ~ 0.08` for this `q`), and the script exits
cleanly with a clear message rather than silently proceeding on bad data.

## Running it

```
Scripts/setup-ssl.sh <N>          # once per party count
python3 Scripts/modconv1_preproc_gen.py <N> <q> <t> \
    --kappa-retries 8 --kappa-stat 16 --seed 1 --verify
```

Drop `--verify` for the real thing (no reveal at all). Persisted output
lands in `Persistence/Transactions-P<i>-q-final.data` (slot
`2*kappa_retries`: `m`) and `Persistence/Transactions-P<i>-t-final.data`
(slots `2*kappa_retries`, `2*kappa_retries+1`: `u_0`, `u_1`) -- copy these
to `Persistence/Transactions-P<i>.data` before a program that does
`sint.read_from_file` at those slots.

## What's not wired up yet: composing with the online phase

`modconv1_online.mpc` (the earlier benchmark) currently expects `u_0, u_1`
as **plain additive shares** read via `personal.read_int` from
`Input-Binary-P<i>` files, because it deliberately kept `t` as a
non-live, purely local domain for performance (see
`modconv1_README.md`) -- a design that matched `modconv1_dealer.py`'s
output shape (raw additive shares) but does **not** match this real
preprocessing's output, which is a genuine *Shamir* sharing over `t` (any
LSSS is valid per the paper's ABB model, but Shamir shares and additive
shares are different representations and don't mix).

Composing this preprocessing with a live online phase therefore needs a
new online-phase variant with `t` as a second live Shamir domain: `y` is
revealed in the q-domain exactly as before, but then has to be carried
(as a public value) into a *separate* t-domain execution that reads the
persisted `u_0`/`u_1`, picks `u_{alpha(y)}` (a public choice for
`beta=2`), and computes `x_t = y - u_{alpha(y)}` -- ordinary local `sint`
arithmetic once `y` is public, and actually simpler than the additive
version (no "only party 0 may add the public value" gate, since Shamir
lets *every* party add a public constant to its own share directly). This
is a natural next step, not attempted here: it requires recompiling the
t-domain online script per conversion (to bake in that conversion's `y`),
which is a different performance profile from the single-compile,
batched-conversions benchmark in `modconv1_README.md`.
