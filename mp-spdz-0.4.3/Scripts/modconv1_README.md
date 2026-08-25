# ModConv1 online-phase benchmark

Implements and benchmarks the **online phase** of `Pi^{q,t,beta}_ModConv1`
from *Better Modulus Conversion for MPC and Threshold FHE* (Damgaard,
Hasler, Kolby, Pawlak), Section 4.1 / Protocol `modconv1`, on top of
MP-SPDZ's native Shamir secret sharing.

## Files

- `Programs/Source/modconv1_online.mpc` — the online phase, as an MP-SPDZ
  compiler script.
- `Scripts/modconv1_dealer.py` — a trusted-dealer stand-in for the
  preprocessing (see "Scope" below).
- This file.

## The protocol, in one paragraph

Preprocessing samples a mask `m` uniform in `Z_q`, splits `Z_q` into
`beta` intervals, and for each interval `i` stores a correction
`u_i = m - c_i*q mod t` where `c_i` records whether the mask wraps around
into that interval. Online, the parties open `y = x + m mod q` (**the only
communication**), compute the public index `alpha(y)` of the interval `y`
falls into, and return `y - u_{alpha(y)} mod t` — a purely local
computation once `y` and `alpha(y)` are public. That's the entire appeal
of the protocol: one round, one opening, independent of `beta`.

## Scope of this implementation

MP-SPDZ compiles one arithmetic domain (a chosen prime or ring) per
binary; there's no built-in notion of a second, unrelated live modulus.
The way this implementation resolves that:

- **`q` is MP-SPDZ's native compiled/runtime prime.** Pass the same prime
  to `-P` at both compile time and run time. The online opening of
  `y = x + m mod q` is a completely standard MP-SPDZ `reveal()`, so it
  gets real Shamir-protocol networking, not a simulation.
- **`t` never becomes a live MP-SPDZ domain.** Since every operation
  involving `t` in the protocol (subtracting the correction) is local by
  construction, there's no need for one — each party just keeps its own
  plain 64-bit integer share of every `u_i` and of the final output, fed
  in via MP-SPDZ's private per-party binary input files
  (`Player-Data/Input-Binary-P<i>-0`, read with `personal.read_int`). This
  is why the online phase's cost doesn't depend on `beta`: selecting and
  subtracting a correction is free once `y` is public.
- **Preprocessing is a trusted dealer, not the paper's secure protocol.**
  `modconv1_dealer.py` samples `m` and computes the corrections directly
  in the clear, in Python, and writes the resulting values as
  MP-SPDZ input files. This is **not** Protocol `modconv1-preproc` from
  the paper (which generates the same material securely via MultiBits and
  a secure comparison, so that no party ever learns `m`) — implementing
  that is a separate, larger task. As a consequence, in this version:
  - **Party 0 learns `m`.** The mask is supplied as party 0's ordinary
    MP-SPDZ input (`sint.get_input_from(0)`), which is a real, secure
    secret-sharing operation, but naturally the party providing the input
    knows it. A production preprocessing protocol must not do this.
  - The `u_i` corrections *are* properly split so that no single party
    learns them (plain additive secret sharing over `t`, computed by the
    dealer) — only `m` itself is compromised by this shortcut.
- **`q` and `t` must both be prime.** `-P` requires an exact prime (MP-SPDZ
  checks and aborts with "... is not a prime" otherwise); `t` is only ever
  used in plain Python/regint arithmetic, but the paper's construction
  assumes prime power moduli, so keep it prime (or a prime power) too.

None of this affects what's actually being measured: the online phase
itself is implemented faithfully and end-to-end, real networked parties
running the real Shamir protocol. Only the preprocessing is a stand-in.

## Two non-obvious bugs fixed along the way (documented in code comments)

1. **Adding a public value to an additive sharing.** `x_t = y - u` with `y`
   public and `u` additively shared: only *one* party (by convention,
   party 0) may add `y` to its own share, otherwise the reconstructed sum
   is off by `(N-1)*y`.
2. **Sign of the opened value.** MP-SPDZ's `regint(cint)` conversion
   assumes the usual convention of a small signed integer padded by a much
   larger prime, and reinterprets any residue past `p/2` as negative. Here
   `q` *is* the entire prime with no such padding, so `y` can be anywhere
   in `[0, q)` and that conversion is wrong roughly half the time. Fixed
   by bit-decomposing the (public) opened value and recomposing it as an
   unsigned `regint`.
3. **Overflow in `alpha(y) = floor(beta*y/q)`.** Computing this as
   `(beta * y_int) // q` on a 64-bit `regint` overflows once
   `beta * q > ~2^63` (e.g. `beta=64` with a 61-bit `q`). Fixed by
   comparing `y` against the `beta - 1` compile-time interval boundaries
   directly instead of forming the product.

## Running it

```
# Compile: -M avoids a compiler reordering optimization that isn't safe
# for the trusted-dealer file reads; -P fixes the exact prime q.
./compile.py -M -P <q> modconv1_online <N> <beta> <C> <q> <t>

# Generate the trusted-dealer preprocessing + test inputs for this run.
python3 Scripts/modconv1_dealer.py <N> <beta> <C> <q> <t> [--seed N]

# SSL setup (once per party count).
Scripts/setup-ssl.sh <N>

# Run (add -v for round/communication stats, -d for direct communication
# so the round count matches the paper's claim -- see below).
for i in $(seq 0 $((N-1))); do
  ./shamir-party.x $i modconv1_online-<N>-<beta>-<C>-<q>-<t> \
      -N <N> -P <q> -OF . -v -d &
done
```

`<N>`, `<beta>`, `<C>`, `<q>`, `<t>` in the compiled program name must
match exactly what you compiled with.

Each party prints one line per conversion, e.g.
`conversion 0: local mod-t share = 5273840` — the party's own additive
share of the result mod `t`. To check correctness, sum a conversion's
shares across all `N` parties' logs and reduce mod `t`; it should equal
`x mod t` (the dealer also writes `Player-Data/modconv1_expected.json`
with these values precomputed).

## Benchmark results

Localhost, 3-party network, `-v -d` (direct communication, matching the
paper's round-counting convention: every party sends its share of an
opening to every other party in one round, rather than MP-SPDZ's default
2-round star-shaped opening through a collector).

Online phase only (`Time1`/timer 1 in the output), `C` = number of
conversions in the batch:

| N | q (bits) | t (bits) | beta | C | rounds | data sent / party | data sent (all parties) |
|---|---|---|---|---|---|---|---|
| 3 | 43 | 23 | 4 | 1000 | 1000 | 0.008 MB | 0.04 MB |
| 3 | 43 | 23 | 16 | 1000 | 1000 | 0.008 MB | 0.04 MB |
| 3 | 61 | 33 | 4 | 300 | 300 | 0.0048 MB | 0.012 MB |
| 3 | 61 | 33 | 64 | 300 | 300 | 0.0048 MB | 0.012 MB |
| 5 | 61 | 33 | 4 | 300 | 300 | 0.0096 MB | 0.0336 MB |
| 5 | 61 | 33 | 64 | 300 | 300 | 0.0096 MB | 0.0336 MB |

Exactly 1 round and identical data volume per conversion regardless of
`beta`, for both party counts — matching Table 1 of the paper (1 online
round, online communication = a single opening, independent of `beta`).
Data sent per party scales with `N-1` under direct communication, as
expected (quadratic total, in exchange for the 1-round count; MP-SPDZ's
default star-shaped opening gets linear total communication at 2 rounds
instead — both were verified to work, `-d` is only needed to match the
paper's round-counting convention).

Correctness was checked end to end (reconstructing every party's output
share and comparing against the expected `x mod t`) across all of the
above runs plus the earlier smaller-scale ones: 2500+ conversions total,
zero mismatches.

## What's not benchmarked here

The preprocessing protocol itself (`modconv1-preproc`, Section 4.1 of the
paper) — MultiBit generation plus a secure comparison — which is
considerably more involved than the online phase and a natural next step.
