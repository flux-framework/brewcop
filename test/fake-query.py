#!/usr/bin/env python3

from __future__ import print_function

import time
import random
import argparse
import itertools

def get_weight(pot, water, fudge_factor):
    if random.random() < 0.5:
        return random.uniform(pot-fudge_factor, pot+water+fudge_factor)
    else:
        return random.uniform(0, fudge_factor)

def output_weight(weight):
    print("{:.04f}".format(weight))

def inf():
    return iter(lambda:0,1)

def main():
    random.seed()

    if args.interval:
        weights = (get_weight(args.pot, args.water, args.tolerance) for x in inf())
        if args.max_iters:
            weights = itertools.islice(weights, 0, args.max_iters - 1)
        for weight in weights:
            output_weight(weight)
    else:
        output_weight(get_weight(args.pot, args.water, args.tolerance))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--interval', type=float,
                        help="continually output weight every X seconds")
    parser.add_argument('-m', '--max-iters', type=int,
                        help="maximum number of weights to output")
    parser.add_argument('-w', '--water', type=float, default=2.6,
                        help="maximum amount of water (or coffee) that the pot can hold (in lbs)")
    parser.add_argument('-p', '--pot', type=float, default=1.6,
                        help="weight (in lbs) of the empty coffee pot")
    parser.add_argument('-t', '--tolerance', type=float, default=0.02,
                        help="error tolerance (in lbs)")
    args = parser.parse_args()

    main()
