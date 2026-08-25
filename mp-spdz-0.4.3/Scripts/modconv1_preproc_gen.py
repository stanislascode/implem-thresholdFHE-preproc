#!/usr/bin/env python3
"""Orchestrate Pi_ModConv1Preproc^{q,t,Q} for beta=2 (paper Section 4.1),
with the auxiliary comparison modulus Q collapsed into t (see
modconv1_preproc_README.md), reusing the MultiBit machinery of
multibits_gen.py for the (q,t) generation+consistency-check part and
adding the real Compare-based accept/retry logic and the final corrections
computation on top.

This produces genuinely secret-shared preprocessing material -- a mask m
shared over q, and corrections u_0, u_1 shared over t -- with no party
ever learning m, replacing modconv1_dealer.py's trusted-dealer stand-in.
Persisted via MP-SPDZ's Persistence mechanism (Persistence/Transactions-
P<i>.data), which this script juggles between the q-domain and t-domain
executions since MP-SPDZ always uses that same fixed path regardless of
which program or modulus is running.

Usage:
  modconv1_preproc_gen.py <n_parties> <q> <t> [--kappa-retries K]
      [--kappa-stat S] [--Q Q] [--seed N] [--verify]

--kappa-retries: number of candidate masks generated per run (default 8;
    each is independently accepted with probability > 1/2, so 8 gives a
    rejection probability below 1/256 -- see thm:modconv1-preproc).
--kappa-stat: MultiBitsConsCheck's statistical security parameter
    (default 16, deliberately small for fast testing -- use 40 for
    anything resembling a real deployment, as in multibits_gen.py).
--verify: also reveal the final (m, u_0, u_1) and check them against the
    plaintext-computed expectation -- TEST ONLY, see the script's own
    warning; never do this against a real deployment's output.
"""
import argparse
import json
import math
import os
import random
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import multibits_gen as mg  # reuse is_prime / next_prime_at_least / helpers

MPSPDZ_DIR = mg.MPSPDZ_DIR


def persistence_paths(n_parties):
    return [os.path.join(MPSPDZ_DIR, 'Persistence', 'Transactions-P%d.data' % i)
            for i in range(n_parties)]


def save_persistence(n_parties, tag):
    os.makedirs(os.path.join(MPSPDZ_DIR, 'Persistence'), exist_ok=True)
    for i, path in enumerate(persistence_paths(n_parties)):
        backup = path[:-5] + '-%s.data' % tag
        if os.path.exists(path):
            shutil.move(path, backup)
        elif os.path.exists(backup):
            pass  # already saved
        else:
            raise RuntimeError('expected Persistence file missing: %s' % path)


def restore_persistence(n_parties, tag):
    for path in persistence_paths(n_parties):
        backup = path[:-5] + '-%s.data' % tag
        shutil.copyfile(backup, path)  # copy, keep the backup around


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('n_parties', type=int)
    ap.add_argument('q', type=int)
    ap.add_argument('t', type=int)
    ap.add_argument('--kappa-retries', type=int, default=8)
    ap.add_argument('--kappa-stat', type=int, default=16)
    ap.add_argument('--Q', type=int, default=None)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--player-data', default='Player-Data')
    ap.add_argument('--port', type=int, default=6000,
                     help='port-number base for shamir-party.x (default '
                          '6000; see multibits_gen.py for why not 5000 -- '
                          'macOS AirPlay Receiver)')
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    N, q, t = args.n_parties, args.q, args.t
    kappa_retries, kappa_stat = args.kappa_retries, args.kappa_stat
    assert mg.is_prime(q) and mg.is_prime(t)
    assert q % 2 == 1, 'q even is not exercised by this implementation'

    k = q.bit_length()               # ceil(log2 q), q not a power of 2
    n = k * kappa_retries             # real MultiBits requested

    small = []
    base = n
    for q_i in (q, t):
        bits = q_i.bit_length()
        tau_i = bits + kappa_stat
        R_i = math.ceil(kappa_stat / bits)
        small.append(dict(modulus=q_i, tau=tau_i, R=R_i, base=base))
        base += R_i * tau_i
    n_prime = base

    tau_ell = math.ceil(math.log2(n + 2 ** (kappa_stat + 1))) + kappa_stat
    if args.Q is not None:
        Qcheck = args.Q
    else:
        bound = max(2 ** (tau_ell + 1) * d['modulus'] for d in small)
        Qcheck = mg.next_prime_at_least(bound + 1)
    for d in small:
        assert Qcheck >= 2 ** (tau_ell + 1) * d['modulus']

    print('k=%d (bits of q) kappa_retries=%d -> n=%d MultiBits requested' %
          (k, kappa_retries, n), file=sys.stderr)
    print('n_prime=%d kappa_stat=%d tau_ell=%d Qcheck has %d bits' %
          (n_prime, kappa_stat, tau_ell, Qcheck.bit_length()), file=sys.stderr)

    # --- Shared per-party input bits (same across q, t, Qcheck), plus
    # each small domain's own extra coin-tossing randomness. ---
    os.makedirs(os.path.join(MPSPDZ_DIR, args.player_data), exist_ok=True)
    bits = [[random.randrange(2) for _ in range(n_prime)] for _ in range(N)]

    def write_inputs(extra_per_party):
        for sigma in range(N):
            path = os.path.join(MPSPDZ_DIR, args.player_data,
                                 'Input-P%d-0' % sigma)
            values = bits[sigma] + extra_per_party[sigma]
            with open(path, 'w') as f:
                f.write(' '.join(str(v) for v in values) + '\n')

    # Plaintext ground truth, for --verify and for our own sanity.
    b_plain = [0] * n_prime
    for kk in range(n_prime):
        v = 0
        for sigma in range(N):
            v ^= bits[sigma][kk]
        b_plain[kk] = v

    # --- Run the q-domain generation. ---
    d_q, d_t = small
    extra_q = [[random.randrange(d_q['modulus']) for _ in range(n * d_q['R'])]
               for _ in range(N)]
    write_inputs(extra_q)
    out_q = mg.compile_and_run(
        'modconv1_preproc_q',
        [N, n, n_prime, d_q['tau'], d_q['R'], d_q['base'], k, kappa_retries],
        N, q, port=args.port)
    d_q['r'] = mg.parse_lines(out_q, 'r', 3, q)
    d_q['z'] = mg.parse_lines(out_q, 'z', 2, q)
    save_persistence(N, 'q')

    # --- Run the t-domain generation + Compare + accept. ---
    extra_t = [[random.randrange(d_t['modulus']) for _ in range(n * d_t['R'])]
               for _ in range(N)]
    write_inputs(extra_t)
    out_t = mg.compile_and_run(
        'modconv1_preproc_t',
        [N, n, n_prime, d_t['tau'], d_t['R'], d_t['base'], k, kappa_retries, q],
        N, t, port=args.port)
    d_t['r'] = mg.parse_lines(out_t, 'r', 3, t)
    d_t['z'] = mg.parse_lines(out_t, 'z', 2, t)
    accept = dict(mg.parse_lines(out_t, 'accept', 2, t))  # {j: 0/1}
    save_persistence(N, 't')

    print('accept bits: %s' % [accept[j] for j in range(kappa_retries)],
          file=sys.stderr)

    # --- MultiBitsConsCheck's large side, exactly as multibits_gen.py --
    # r-vectors go through a JSON side file, not positional args, since n
    # (= k * kappa_retries here) is far too large to embed in compile.py's
    # own args-in-filename naming convention without hitting the OS
    # filename length limit. ---
    r_data = []
    for d in small:
        r_by_rho = {}
        for rho, kk, v in d['r']:
            r_by_rho.setdefault(rho, {})[kk] = v
        r_data.append([[r_by_rho[rho][kk] for kk in range(n)]
                       for rho in range(d['R'])])
    r_data_path = os.path.join(MPSPDZ_DIR, 'modconv1_preproc_r_data.json')
    with open(r_data_path, 'w') as f:
        json.dump(r_data, f)

    large_args = [N, n, n_prime, tau_ell, len(small)]
    for d in small:
        large_args += [d['modulus'], d['tau'], d['R'], d['base']]
    large_args += [r_data_path, 0]  # reveal_for_test = 0: never reveal m's bits
    write_inputs([[] for _ in range(N)])
    large_out = mg.compile_and_run('modconv1_preproc_check', large_args, N,
                                    Qcheck, port=args.port)
    z_tilde = mg.parse_lines(large_out, 'ztilde', 3, Qcheck)

    ok = True
    for dom_idx, d in enumerate(small):
        z_by_rho = dict(d['z'])
        for dz_idx, rho, zt in z_tilde:
            if dz_idx != dom_idx:
                continue
            lhs, rhs = z_by_rho[rho] % d['modulus'], zt % d['modulus']
            if lhs != rhs:
                ok = False
            print('consistency check domain=%d rho=%d: %s' %
                  (dom_idx, rho, 'OK' if lhs == rhs else 'MISMATCH'),
                  file=sys.stderr)
    if not ok:
        print('MultiBitsConsCheck FAILED -- aborting preprocessing '
              '(this should happen with probability at most 2^-kappa_stat)')
        sys.exit(1)

    # --- Pick the first accepted candidate. ---
    r_star = next((j for j in range(kappa_retries) if accept[j] == 1), None)
    if r_star is None:
        print('All %d candidates rejected (probability < %.2e) -- abort, '
              're-run with more --kappa-retries or bad luck.' %
              (kappa_retries, (1 - q / 2 ** k) ** kappa_retries))
        sys.exit(1)
    print('accepted candidate r_star = %d' % r_star, file=sys.stderr)

    # --- Finalize: compute and persist the real m (over q) and u_0, u_1
    # (over t) for the accepted candidate. Nothing opened. ---
    restore_persistence(N, 'q')
    mg.compile_and_run('modconv1_preproc_finalize_q', [q, kappa_retries, r_star],
                        N, q, port=args.port)
    save_persistence(N, 'q-final')

    restore_persistence(N, 't')
    mg.compile_and_run('modconv1_preproc_finalize_t', [q, kappa_retries, r_star],
                        N, t, port=args.port)
    save_persistence(N, 't-final')

    print('preprocessing produced: <m>_q at Persistence-P*-q-final.data '
          '(slot %d), <u_0>_t, <u_1>_t at Persistence-P*-t-final.data '
          '(slots %d, %d)' % (2 * kappa_retries, 2 * kappa_retries,
                               2 * kappa_retries + 1))

    if not args.verify:
        return

    # --- TEST ONLY: reveal and cross-check against plaintext. Never do
    # this against a real deployment's preprocessing output. ---
    print('\n--verify: revealing (m, u_0, u_1) for a plaintext cross-check '
          '-- TEST ONLY.', file=sys.stderr)
    restore_persistence(N, 'q-final')
    out_vq = mg.compile_and_run('modconv1_preproc_verify_q', [kappa_retries],
                                 N, q, port=args.port)
    m_revealed = mg.parse_lines(out_vq, 'm', 1, q)[0][0]

    restore_persistence(N, 't-final')
    out_vt = mg.compile_and_run('modconv1_preproc_verify_t', [kappa_retries],
                                 N, t, port=args.port)
    u0_revealed, u1_revealed = [v for _, v in
                                 mg.parse_lines(out_vt, 'u', 2, t)]

    batch = b_plain[r_star * k:(r_star + 1) * k]
    m_L_expected = sum((1 << i) * batch[i] for i in range(k - 1))
    m_U_expected = batch[k - 1]
    ceil_q_2 = -(-q // 2)
    m_expected = m_L_expected + ceil_q_2 * m_U_expected
    u0_expected = (m_expected - q * m_U_expected) % t
    u1_expected = m_expected % t

    print('m:   revealed=%d expected=%d %s' %
          (m_revealed, m_expected,
           'OK' if m_revealed == m_expected else 'MISMATCH'))
    print('u_0: revealed=%d expected=%d %s' %
          (u0_revealed, u0_expected,
           'OK' if u0_revealed == u0_expected else 'MISMATCH'))
    print('u_1: revealed=%d expected=%d %s' %
          (u1_revealed, u1_expected,
           'OK' if u1_revealed == u1_expected else 'MISMATCH'))
    assert 0 <= m_expected < q
    if (m_revealed, u0_revealed, u1_revealed) == \
            (m_expected, u0_expected, u1_expected):
        print('modconv1-preproc: PASS')
    else:
        print('modconv1-preproc: FAIL')
        sys.exit(1)


if __name__ == '__main__':
    main()
