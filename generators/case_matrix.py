"""
64-case reconciliation matrix definition.
Each case defines what value (1, -1, 0, null) each file carries
and what data rows that translates to.

Value semantics:
  NFS:    1=bank debited by NFS (txn),  -1=bank credited by NFS (reversal),
          0=net zero (original+rev in file),  null=no entry
  Switch: 1=approved/forwarded,          -1=reversal forwarded,
          0=both original+rev in file,   null=no entry
  CBS:    1=customer debited,            -1=customer credited (refund),
          0=net zero (original+rev),     null=no entry

Row generation per value:
  1    → one forward transaction row (approved, positive amount)
  -1   → one reversal row (msg_type 0420 / credit sign)
  0    → two rows: forward + reversal (same RRN, net zero)
  null → no row written for this file
"""

CASES = [
    # case_id, nfs, switch, cbs, action, is_ok
    ( 1,  1,  1,  1,  "OK",                               True),
    ( 2,  1,  1, -1,  "CBS reversal — recover from customer", False),
    ( 3,  1,  1,  0,  "CBS net zero — recover from customer", False),
    ( 4,  1,  1, None,"CBS missing — wait, then recover",  False),
    ( 5,  1, -1,  1,  "OK",                               True),
    ( 6,  1, -1, -1,  "CBS reversal — recover from customer", False),
    ( 7,  1, -1,  0,  "CBS net zero — recover from customer", False),
    ( 8,  1, -1, None,"CBS missing — wait, then recover",  False),
    ( 9,  1,  0,  1,  "OK",                               True),
    (10,  1,  0, -1,  "CBS reversal — recover from customer", False),
    (11,  1,  0,  0,  "CBS net zero — recover from customer", False),
    (12,  1,  0, None,"CBS missing — wait, then recover",  False),
    (13,  1, None, 1, "OK",                               True),
    (14,  1, None,-1, "CBS reversal — recover from customer", False),
    (15,  1, None, 0, "CBS net zero — recover from customer", False),
    (16,  1, None,None,"NFS only — wait, then recover",   False),
    (17, -1,  1,  1,  "NFS reversal, CBS debit — refund customer", False),
    (18, -1,  1, -1,  "OK",                               True),
    (19, -1,  1,  0,  "CBS net zero — refund customer",   False),
    (20, -1,  1, None,"CBS missing — wait, then refund",  False),
    (21, -1, -1,  1,  "NFS+Switch reversal, CBS debit — refund", False),
    (22, -1, -1, -1,  "OK",                               True),
    (23, -1, -1,  0,  "CBS net zero — refund customer",   False),
    (24, -1, -1, None,"CBS missing — wait, then refund",  False),
    (25, -1,  0,  1,  "NFS reversal, CBS debit — refund", False),
    (26, -1,  0, -1,  "OK",                               True),
    (27, -1,  0,  0,  "CBS net zero — refund customer",   False),
    (28, -1,  0, None,"CBS missing — wait, then refund",  False),
    (29, -1, None, 1, "NFS reversal, CBS debit — refund", False),
    (30, -1, None,-1, "OK",                               True),
    (31, -1, None, 0, "CBS net zero — refund customer",   False),
    (32, -1, None,None,"NFS reversal only — wait, then refund", False),
    (33,  0,  1,  1,  "NFS net zero, CBS debit — refund", False),
    (34,  0,  1, -1,  "NFS net zero, CBS credit — recover", False),
    (35,  0,  1,  0,  "OK",                               True),
    (36,  0,  1, None,"NFS net zero, CBS missing — wait", False),
    (37,  0, -1,  1,  "NFS net zero, CBS debit — refund", False),
    (38,  0, -1, -1,  "NFS net zero, CBS credit — recover", False),
    (39,  0, -1,  0,  "OK",                               True),
    (40,  0, -1, None,"NFS net zero, CBS missing — wait", False),
    (41,  0,  0,  1,  "All balanced but CBS debit — refund", False),
    (42,  0,  0, -1,  "All balanced but CBS credit — recover", False),
    (43,  0,  0,  0,  "OK",                               True),
    (44,  0,  0, None,"CBS missing — wait",               False),
    (45,  0, None, 1, "NFS net zero, CBS debit — refund", False),
    (46,  0, None,-1, "NFS net zero, CBS credit — recover", False),
    (47,  0, None, 0, "OK",                               True),
    (48,  0, None,None,"NFS net zero — wait",             False),
    (49, None, 1,  1, "NFS missing — wait, then refund",  False),
    (50, None, 1, -1, "NFS missing — wait, then recover", False),
    (51, None, 1,  0, "NFS missing — wait",               False),
    (52, None, 1, None,"Switch only — OK",                True),
    (53, None,-1,  1, "NFS missing — wait, then refund",  False),
    (54, None,-1, -1, "NFS missing — wait, then recover", False),
    (55, None,-1,  0, "NFS missing — wait",               False),
    (56, None,-1, None,"Switch reversal only — OK",       True),
    (57, None, 0,  1, "NFS missing — wait, then refund",  False),
    (58, None, 0, -1, "NFS missing — wait, then recover", False),
    (59, None, 0,  0, "NFS missing — wait",               False),
    (60, None, 0, None,"Switch net zero — OK",            True),
    (61, None,None, 1, "CBS debit, nothing else — wait, refund", False),
    (62, None,None,-1, "CBS credit, nothing else — wait, recover", False),
    (63, None,None, 0, "CBS net zero, nothing else — wait", False),
    (64, None,None,None,"All missing — OK",               True),
]

# Quick lookup
CASE_MAP = {c[0]: c for c in CASES}
OK_CASES     = [c[0] for c in CASES if c[5]]
NON_OK_CASES = [c[0] for c in CASES if not c[5]]
