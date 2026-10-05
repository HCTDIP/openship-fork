"""jevaudit.audit — independent checks of a ledger written by someone else (e.g. a chain listener).

The brake (jevaudit.hctdip) judges each decision while it happens. It cannot see data that never
reached it. These functions re-read the source afterwards and compare:

    verify_ledger_integrity(records, known_code_hashes)   three fingerprints per record
    reconcile_with_chain(records, rpcs, lo, hi, wallets)    fill-by-fill vs. 3 independent RPC nodes

CLI:  python -m jevaudit.audit reconcile --help
Note: jevaudit.verify.verify_ledger_integrity is the older check for Jev decision ledgers
(different record format); this one is for on-chain fill ledgers.
"""
from jevaudit.audit.chain import verify_ledger_integrity, reconcile_with_chain, DEFAULT_RPCS

__all__ = ["verify_ledger_integrity", "reconcile_with_chain", "DEFAULT_RPCS"]
