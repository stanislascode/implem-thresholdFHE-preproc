#!/usr/bin/env python3
"""
Trusted-dealer stand-in for the preprocessing of Pi^{q,t,beta}_ModConv1
(Damgaard, Hasler, Kolby, Pawlak, "Better Modulus Conversion for MPC and
Threshold FHE", Protocol modconv1 preprocessing steps 1-3).

This is NOT the paper's secure preprocessing protocol (which uses MultiBits
and a secure comparison so that no party learns m). It is a fast, insecure
stand-in whose only job is to get real numbers out of the ONLINE phase
today. See modconv1_online.mpc and README.md for how the two fit together.

For each of n_conversions independent conversions it:
  - samples m uniformly in Z_q,
  - computes the beta interval corrections c_i, u_i = (m - c_i*q) mod t,
  - additively secret-shares each u_i across the N parties mod t,
  - samples a real input x uniformly in the protocol's promised range
    [0, q - ceil(q/beta)], so correctness of the online phase can be
    checked end to end.

Output (matching what Programs/Source/modconv1_online.mpc reads):
  Player-Data/Input-P0-0            m_0 .. m_{C-1}           (decimal text)
  Player-Data/Input-P1-0            x_0 .. x_{C-1}           (decimal text)
  Player-Data/Input-Binary-P<s>-0   party s's own share of every u_i,
                                     little-endian signed 64-bit ints, in
                                     the order: for c in range(C): for i in
                                     range(beta): share(c, i)

It also writes modconv1_expected.json with, for each conversion, the
expected x mod t, so a test harness can check the online phase's output
(summed across all N parties' printed local shares) without re-deriving
the math itself.
"""
import argparse
import json
import os
import random
import struct


def interval_index(value, modulus, n_intervals):
    return (n_intervals * value) // modulus


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('n_parties', type=int)
    p.add_argument('beta', type=int)
    p.add_argument('n_conversions', type=int)
    p.add_argument('q', type=int)
    p.add_argument('t', type=int)
    p.add_argument('--player-data', default='Player-Data',
                    help='directory to write Player-Data files into')
    p.add_argument('--seed', type=int, default=None)
    args = p.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    N, beta, C, q, t = (args.n_parties, args.beta, args.n_conversions,
                        args.q, args.t)
    assert beta >= 2
    assert N >= 3
    bound = q - (-(-q // beta))  # q - ceil(q/beta), the protocol's precondition

    os.makedirs(args.player_data, exist_ok=True)

    m_values = []
    x_values = []
    expected = []
    # party_shares[sigma] is the flat list of int64 shares for that party,
    # in (c, i) order.
    party_shares = [[] for _ in range(N)]

    for c in range(C):
        m = random.randrange(q)
        x = random.randrange(bound + 1)  # respects x <= q - ceil(q/beta)
        m_values.append(m)
        x_values.append(x)
        expected.append(x % t)

        alpha_m = interval_index(m, q, beta)
        for i in range(beta):
            c_i = 1 if alpha_m > i else 0
            u_i = (m - c_i * q) % t

            shares = [random.randrange(t) for _ in range(N - 1)]
            last = (u_i - sum(shares)) % t
            shares.append(last)
            assert sum(shares) % t == u_i

            for sigma in range(N):
                party_shares[sigma].append(shares[sigma])

    with open(os.path.join(args.player_data, 'Input-P0-0'), 'w') as f:
        f.write(' '.join(str(m) for m in m_values) + '\n')

    with open(os.path.join(args.player_data, 'Input-P1-0'), 'w') as f:
        f.write(' '.join(str(x) for x in x_values) + '\n')

    for sigma in range(N):
        path = os.path.join(args.player_data, f'Input-Binary-P{sigma}-0')
        with open(path, 'wb') as f:
            for v in party_shares[sigma]:
                # signed little-endian 64-bit, matching fixinput_int<int64_t>
                f.write(struct.pack('<q', v))

    summary = {
        'n_parties': N, 'beta': beta, 'n_conversions': C, 'q': q, 't': t,
        'bound': bound, 'x_values': x_values, 'expected_x_mod_t': expected,
    }
    with open(os.path.join(args.player_data, 'modconv1_expected.json'),
              'w') as f:
        json.dump(summary, f, indent=2)

    print(f'Wrote preprocessing + inputs for {C} conversion(s), '
          f'N={N}, beta={beta}, q={q}, t={t}, bound={bound}.')
    print(f'Expected x mod t: {expected}')


if __name__ == '__main__':
    main()
