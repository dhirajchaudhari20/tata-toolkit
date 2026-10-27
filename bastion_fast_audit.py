"""
====================================================================================================
           TATA COMMUNICATIONS - BASTION TO DIRECTOR RAPID BATCH AUDIT SCRIPT
====================================================================================================
Designed and Developed by Tata Communications
Runs directly on PU1PAMMCPSGW01 or through the automated terminal.
Audits all 20 routers, pings the target sites, evaluates SLAs, and outputs Excel/CSV.
====================================================================================================
"""

import sys
import time
import re
import os
import ipaddress
from datetime import datetime

DIRECTOR_IP = "10.29.11.32"
DIRECTOR_USER = "admin"
PASSWORD = r"e#mi$Wp@U8%f"

TARGET_SITES = [
    "IFT-MB-3007-PDCKHR-R1",
    "IFT-MB-3038-CCILDD-R1"
]

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
print(" TATA COMMUNICATIONS - 20 ROUTER BATCH SD-WAN HEALTH AUDIT")
print("=" * 80)
print(f"Target Sites : {', '.join(TARGET_SITES)}")
print(f"Total Routers: {len(ROUTERS)}")
print("=" * 80)
