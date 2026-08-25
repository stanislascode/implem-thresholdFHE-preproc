#!/usr/bin/env python3
"""Generate test inputs for xorbits.mpc: n_parties random 0/1 vectors of
length n_bits, written as MP-SPDZ plaintext Input-P<i>-0 files, plus the
expected bitwise XOR across parties for correctness-checking.

Usage: xorbits_gen_inputs.py <n_parties> <n_bits> [--seed N]
       [--player-data DIR]
"""
import argparse
import json
import os
import random

parser = argparse.ArgumentParser()
parser.add_argument('n_parties', type=int)
parser.add_argument('n_bits', type=int)
parser.add_argument('--seed', type=int, default=None)
parser.add_argument('--player-data', default='Player-Data')
args = parser.parse_args()

if args.seed is not None:
    random.seed(args.seed)

N, n = args.n_parties, args.n_bits
os.makedirs(args.player_data, exist_ok=True)

bits = [[random.randrange(2) for _ in range(n)] for _sigma in range(N)]

for sigma in range(N):
    path = os.path.join(args.player_data, 'Input-P%d-0' % sigma)
    with open(path, 'w') as f:
        f.write(' '.join(str(b) for b in bits[sigma]) + '\n')

expected = [0] * n
for k in range(n):
    v = 0
    for sigma in range(N):
        v ^= bits[sigma][k]
    expected[k] = v

with open(os.path.join(args.player_data, 'xorbits_expected.json'), 'w') as f:
    json.dump({'bits': bits, 'expected': expected}, f, indent=2)

print('Wrote %d-party, %d-bit inputs; expected XOR: %s' % (N, n, expected))
