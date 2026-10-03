#!/usr/bin/env python3
"""jevkit CLI — 命令行入口。

用法:
    jevkit noul "worth outreach?" --state "lead: ..."
    jevkit gate 0.63
    jevkit decide questions.json --state "..."
"""
import argparse
import json
import sys

from .client import Client, gate


def main():
    ap = argparse.ArgumentParser(prog='jevkit',
                                 description='Jev decision model CLI (calibrated probabilities, not chat)')
    sub = ap.add_subparsers(dest='cmd')

    # noul
    p_noul = sub.add_parser('noul', help='yes/no calibrated probability')
    p_noul.add_argument('question', help='是/否问题')
    p_noul.add_argument('--state', default='', help='上下文')
    p_noul.add_argument('--name', default='q', help='question name')
    p_noul.add_argument('--true-desc', default='The condition described holds.', help='true criteria')
    p_noul.add_argument('--false-desc', default='The condition described does not hold.', help='false criteria')

    # gate
    p_gate = sub.add_parser('gate', help='act/confirm/escalate decision gate')
    p_gate.add_argument('p', type=float, help='probability 0.0-1.0')
    p_gate.add_argument('--act', type=float, default=0.7)
    p_gate.add_argument('--confirm', type=float, default=0.5)

    # decide
    p_dec = sub.add_parser('decide', help='full decisions call (questions record JSON)')
    p_dec.add_argument('questions', help='questions record JSON 文件路径')
    p_dec.add_argument('--state', default='', help='上下文')

    args = ap.parse_args()

    if args.cmd == 'noul':
        client = Client()
        criteria = {'true': args.true_desc, 'false': args.false_desc}
        p = client.noul(args.name, args.question, criteria, state=args.state)
        action = gate(p)
        print(f'{p:.2f} → {action}')
    elif args.cmd == 'gate':
        print(gate(args.p, act=args.act, confirm=args.confirm))
    elif args.cmd == 'decide':
        client = Client()
        questions = json.loads(open(args.questions).read())
        r = client.decide(questions, state=args.state)
        print(json.dumps(r.get('answers', r), ensure_ascii=False, indent=2))
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
