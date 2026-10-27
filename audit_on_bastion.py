#!/usr/bin/env python3
"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
               VERSA SD-WAN AUTOMATED ROUTER HEALTH AUDIT (BASTION LINUX RUNNER)
====================================================================================================
Designed and Developed by Tata Communications
Runs directly on PU1PAMMCPSGW01 to audit all 20 Versa SD-WAN routers via 10.29.11.32.
====================================================================================================
"""

import sys
import time
import re
import os
import csv
from datetime import datetime

# Versa Director details
DIRECTOR_IP = "10.29.11.32"
PASSWORD = r"e#mi$Wp@U8%f"

# 20 Branch Router Hostnames
ROUTERS = [
    "1074-KAIJSB-KOLHAPUR-R1",
    "1074-KAIJSB-KOLHAPUR-R2",
    "1074-KAIJSB-MUM-R1",
    "1074-KAIJSB-MUM-R2",
    "1081-LICMUTFAM-MUM-R1",
    "1081-LICMUTFAM-MUM-R2",
    "1097-UTIASTMGT-MUM-R1",
    "1250-ICICILOM-MUMPRBDV-R1",
    "1250-ICICILOM-MUMPRBDV-R2",
    "1250-ICICILOM-MUMVASHI-R1",
    "1250-ICICILOM-MUMVASHI-R2",
    "1196-NASMERC-NASHIK-R1",
    "1196-NASMERC-NASHIK-R2",
    "1196-NASMERC-PUNE-R1",
    "1196-NASMERC-PUNE-R2",
    "1343-COASTALBANK-MUM-R1",
    "1343-COASTALBANK-MUM-R2",
    "1343-COASTALBANK-BLR-R1",
    "1343-COASTALBANK-BLR-R2",
    "1015-CEYLON-BLR-R1"
]

print("=" * 80)
print(" TATA COMMUNICATIONS - 20 ROUTER SD-WAN AUTOMATED HEALTH AUDIT")
print("=" * 80)
print(f"Versa Director : {DIRECTOR_IP}")
print(f"Total Routers  : {len(ROUTERS)}")
print("=" * 80)
