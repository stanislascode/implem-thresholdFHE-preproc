#!/usr/bin/env python3
"""Orchestrate a full run of MultiBit generation over two similar-size
moduli (q, t), via an auxiliary large modulus Q for the consistency check
(see multibits_README.md and Protocol MultiBitsConsCheck in the paper).

Runs three separate MP-SPDZ compilations/executions (domain q, domain t,
domain Q) against a single shared set of per-party input bits, and stitches
the results together: the public randomness and openings that
multibits_small.mpc produces for q and t are fed as compile-time constants
into multibits_large.mpc for Q, and the final z vs z~ comparison (already
public values by that point) is done here in plain Python.

Usage:
  multibits_gen.py <n_parties> <n> <q> <t> [--kappa K] [--Q Q] [--seed S]

<n_parties>: N
<n>: number of real MultiBits to produce
<q>, <t>: the two similar-size moduli (must be prime)
--kappa: statistical security parameter for the consistency check
         (default 16 -- deliberately small for fast testing; a real
         deployment should use 40).
--Q: override the auxiliary large prime (default: search for the smallest
     prime satisfying the precondition of Protocol MultiBitsConsCheck).
"""
import argparse
import gmpy2
import json
import math
import os
import random
import re
import subprocess
import sys

MPSPDZ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_prime(n):
    # gmpy2's Miller-Rabin (GMP's mpz_probab_prime_p under the hood, 25
    # rounds by default) -- a C-fast, battle-tested replacement for the
    # hand-rolled Python Miller-Rabin this used to be. Same guarantee
    # (negligible false-positive probability), much faster for large n.
    return bool(gmpy2.is_prime(n))


def next_prime_at_least(n):
    # gmpy2.next_prime(x) returns the smallest probable prime strictly
    # greater than x, so next_prime(n - 1) is the smallest prime >= n
    # (inclusive) -- same semantics this used to get from a hand-rolled
    # +2 stepping loop over is_prime().
    return int(gmpy2.next_prime(n - 1))


def run(cmd, **kwargs):
    print('+ ' + ' '.join(str(c) for c in cmd), file=sys.stderr)
    return subprocess.run(cmd, cwd=MPSPDZ_DIR, capture_output=True,
                           text=True, **kwargs)


# Lines compile.py -M and shamir-party.x already print on their own --
# static instruction counts at compile time, wall-clock time and
# communication volume at run time -- that compile_and_run() used to
# throw away entirely (stdout was only ever surfaced on failure). Used by
# print_stats() below to answer "how much work / how long did this
# program actually take" without hand-instrumenting anything.
STATS_PATTERNS = (
    re.compile(r'^\s*[\d,]+\s+integer\b'),           # e.g. "990 integer triples"
    re.compile(r'^\s*[\d,]+\s+virtual machine rounds'),
    re.compile(r'^Time\s*='),
    re.compile(r'^Data sent\s*='),
    re.compile(r'^Global data sent\s*='),
)


def print_stats(label, text):
    for line in text.splitlines():
        if any(p.search(line) for p in STATS_PATTERNS):
            print('    [stats %s] %s' % (label, line.strip()), file=sys.stderr)


def compile_and_run(program, args, n_parties, modulus, port=6000):
    # Must mirror compile.py's own naming (Compiler/program.py's
    # init_names): it derives the compiled program's name by joining every
    # positional arg with '-', substituting '_' for any '/' first (so a
    # filesystem path passed as an arg doesn't produce literal slashes in
    # a filename). shamir-party.x needs this exact name to find the
    # compiled schedule.
    name = '-'.join([program] +
                     [re.sub('/', '_', str(a)) for a in args])
    cp = run(['./compile.py', '-M', '-P', str(modulus), program] +
             [str(a) for a in args])
    if cp.returncode != 0:
        print(cp.stdout)
        print(cp.stderr)
        raise RuntimeError('compile failed for %s' % name)
    print_stats(name + ' (compile-time)', cp.stdout)

    procs = []
    for i in range(n_parties):
        procs.append(subprocess.Popen(
            # -pn overrides MP-SPDZ's default port-number base of 5000,
            # which on macOS collides with the AirPlay Receiver service
            # that listens there by default -- shamir-party.x then spins
            # retrying "Address already in use" forever, which looks like
            # a hang rather than an error. 6000 is just a port unlikely to
            # already be taken; override with --port if it also collides.
            ['./shamir-party.x', str(i), name, '-N', str(n_parties),
             '-P', str(modulus), '-OF', '.', '-pn', str(port)],
            cwd=MPSPDZ_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True))
    outputs = [p.communicate()[0] for p in procs]
    for i, p in enumerate(procs):
        if p.returncode != 0:
            print(outputs[i])
            raise RuntimeError('party %d failed for %s' % (i, name))
    print_stats(name + ' (party 0 runtime)', outputs[0])
    return outputs[0]  # all parties see the same public values


def parse_lines(output, tag, n_fields, modulus=None):
    # MP-SPDZ's print_ln shows revealed values in signed representation
    # (residues past modulus/2 print negative), the same convention that
    # bit us with y in modconv1_online.mpc. Since everything printed here
    # is actually meant to be used as a canonical residue mod its own
    # domain, normalize with Python's floor-mod (always non-negative for a
    # positive modulus) rather than re-deriving the unsigned value inside
    # the .mpc script.
    out = []
    for line in output.splitlines():
        parts = line.split()
        if parts and parts[0] == tag:
            values = [int(v) for v in parts[1:1 + n_fields]]
            if modulus is not None:
                values[-1] = values[-1] % modulus
            out.append(tuple(values))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('n_parties', type=int)
    ap.add_argument('n', type=int)
    ap.add_argument('q', type=int)
    ap.add_argument('t', type=int)
    ap.add_argument('--kappa', type=int, default=16)
    ap.add_argument('--Q', type=int, default=None)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--player-data', default='Player-Data')
    ap.add_argument('--port', type=int, default=6000,
                     help='port-number base for shamir-party.x (default '
                          '6000, to avoid colliding with the default 5000 '
                          '-- see AirPlay Receiver note in compile_and_run)')
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    N, n, q, t, kappa = args.n_parties, args.n, args.q, args.t, args.kappa
    assert is_prime(q), 'q must be prime'
    assert is_prime(t), 'q must be prime'

    small = []  # list of dicts: modulus, tau, R, base
    base = n
    for q_i in (q, t):
        bits = q_i.bit_length()
        tau_i = bits + kappa
        R_i = math.ceil(kappa / bits)
        small.append(dict(modulus=q_i, tau=tau_i, R=R_i, base=base))
        base += R_i * tau_i
    n_prime = base

    tau_ell = math.ceil(math.log2(n + 2 ** (kappa + 1))) + kappa

    if args.Q is not None:
        Q = args.Q
    else:
        bound = max(2 ** (tau_ell + 1) * d['modulus'] for d in small)
        Q = next_prime_at_least(bound + 1)
    for d in small:
        assert Q >= 2 ** (tau_ell + 1) * d['modulus'], \
            'Q does not satisfy the MultiBitsConsCheck precondition'

    print('n=%d n_prime=%d kappa=%d tau_ell=%d Q has %d bits' %
          (n, n_prime, kappa, tau_ell, Q.bit_length()), file=sys.stderr)
    for label, d in zip(('q', 't'), small):
        print('  %s: %d bits, tau=%d, R=%d, base=%d' %
              (label, d['modulus'].bit_length(), d['tau'], d['R'], d['base']),
              file=sys.stderr)

    # Shared per-party input bits: the n_prime-long bit vector is the same
    # across all three executions (that's what makes q, t and Q sharings of
    # the same underlying MultiBits). Each small domain additionally needs
    # its own extra per-party randomness for the coin-tossed challenge r_i
    # -- domain-specific, not shared, so it's appended fresh before that
    # domain's own run and doesn't need to match across domains.
    os.makedirs(os.path.join(MPSPDZ_DIR, args.player_data), exist_ok=True)
    bits = [[random.randrange(2) for _ in range(n_prime)] for _ in range(N)]

    def write_inputs(extra_per_party):
        for sigma in range(N):
            path = os.path.join(MPSPDZ_DIR, args.player_data,
                                 'Input-P%d-0' % sigma)
            values = bits[sigma] + extra_per_party[sigma]
            with open(path, 'w') as f:
                f.write(' '.join(str(v) for v in values) + '\n')

    expected = [0] * n
    for k in range(n):
        v = 0
        for sigma in range(N):
            v ^= bits[sigma][k]
        expected[k] = v
    print('expected XOR (first n bits): %s' % expected, file=sys.stderr)

    # --- Run the two small domains. ---
    small_outputs = []
    for label, d in zip(('q', 't'), small):
        n_extra = n * d['R']
        extra = [[random.randrange(d['modulus']) for _ in range(n_extra)]
                 for _ in range(N)]
        write_inputs(extra)
        out = compile_and_run(
            'multibits_small',
            [N, n, n_prime, d['tau'], d['R'], d['base'], 1], N, d['modulus'],
            port=args.port)
        small_outputs.append(out)
        d['r'] = parse_lines(out, 'r', 3, d['modulus'])   # (rho, k, value)
        d['z'] = parse_lines(out, 'z', 2, d['modulus'])   # (rho, value)
        d['b'] = parse_lines(out, 'b', 2)                 # (k, value)
        print('  domain %s: b prefix = %s' %
              (label, [v for _, v in d['b']]), file=sys.stderr)

    # --- Assemble the large-domain args. The r-vectors go through a JSON
    # side file rather than positional args -- see multibits_large.mpc's
    # header for why (compile.py's own naming convention appends every
    # positional arg to the compiled program's filename, which overflows
    # the OS filename length limit once n grows past a few dozen). ---
    r_data = []
    for d in small:
        r_by_rho = {}
        for rho, k, v in d['r']:
            r_by_rho.setdefault(rho, {})[k] = v
        r_data.append([[r_by_rho[rho][k] for k in range(n)]
                       for rho in range(d['R'])])
    r_data_path = os.path.join(MPSPDZ_DIR, 'multibits_r_data.json')
    with open(r_data_path, 'w') as f:
        json.dump(r_data, f)

    large_args = [N, n, n_prime, tau_ell, len(small)]
    for d in small:
        large_args += [d['modulus'], d['tau'], d['R'], d['base']]
    large_args += [r_data_path, 1]  # 1 = reveal_for_test
    write_inputs([[] for _ in range(N)])
    large_out = compile_and_run('multibits_large', large_args, N, Q,
                                 port=args.port)
    z_tilde = parse_lines(large_out, 'ztilde', 3, Q)  # (dom_idx, rho, value)
    b_Q = parse_lines(large_out, 'b', 2)
    print('  domain Q: b prefix = %s' % [v for _, v in b_Q], file=sys.stderr)

    # --- Final comparison: two already-public values, plain Python. ---
    ok = True
    for dom_idx, d in enumerate(small):
        z_by_rho = dict(d['z'])
        for dz_idx, rho, zt in z_tilde:
            if dz_idx != dom_idx:
                continue
            z = z_by_rho[rho]
            lhs = z % d['modulus']
            rhs = zt % d['modulus']
            status = 'OK' if lhs == rhs else 'MISMATCH'
            if lhs != rhs:
                ok = False
            print('check domain=%d rho=%d : z mod q_i=%d  ztilde mod q_i=%d  %s'
                  % (dom_idx, rho, lhs, rhs, status), file=sys.stderr)

    b_q_prefix = [v for _, v in small[0]['b']]
    b_t_prefix = [v for _, v in small[1]['b']]
    b_Q_prefix = [v for _, v in b_Q]
    agree = b_q_prefix == b_t_prefix == b_Q_prefix == expected
    print('prefixes agree across q, t, Q and match expected XOR: %s' % agree,
          file=sys.stderr)

    if ok and agree:
        print('MultiBitsConsCheck: PASS (%d real MultiBits produced)' % n)
        sys.exit(0)
    else:
        print('MultiBitsConsCheck: FAIL')
        sys.exit(1)


if __name__ == '__main__':
    main()
