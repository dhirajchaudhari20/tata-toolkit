"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
            AUTOMATED VERSA SD-WAN ROUTER-TO-ROUTER DIAGNOSTICS & PING AUDIT
====================================================================================================
Designed and Developed by Tata Communications / Advanced Network Engineering
Author: Senior Network Reliability Engineer
Target Environment: Versa Director (22.1.4 GA) & Versa FlexVNF Branch Routers
====================================================================================================
"""

import sys
import time
import re
import os
import ipaddress
from datetime import datetime
import pandas as pd

# Check for paramiko
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False


# ==================================================================================================
# DEFAULT CONFIGURATION - CUSTOMIZE ACCORDING TO YOUR ENVIRONMENT
# ==================================================================================================
DEFAULT_CONFIG = {
    # Bastion / Jump Host (PU1PAMMCPSGW01)
    "jump_host": "PU1PAMMCPSGW01",
    "jump_user": "mcpeuser1",
    "jump_password": "",  # Prompted securely if blank

    # Versa Director (Central Controller)
    "director_ip": "10.29.11.32",
    "director_user": "admin",
    "director_password": "e#mi$Wp@U8%f",  # Pre-saved credentials

    # Branch Router Credentials
    "router_user": "admin",
    "router_password": "e#mi$Wp@U8%f",    # Pre-saved credentials

    # Branch Router Name to Audit
    "source_branch": "1074-KAIJSB-KOLHAPUR-R1",

    # Target Remote Sites to Benchmark Against
    "target_sites": [
        "IFT-MB-3007-PDCKHR-R1",  # Primary Data Center Kharghar Router 1
        "IFT-MB-3038-CCILDD-R1",  # CCILDD Data Center Router 1
    ],

    # Service Level Agreement (SLA) Thresholds
    "sla_max_packet_loss_pct": 0.0,   # Must be 0% loss
    "sla_max_avg_latency_ms": 50.0,   # Avg latency must be <= 50.0 ms

    # Rapid Ping Parameters
    "ping_count": 500,
    "ping_packet_size": 1472,  # 1472 bytes payload = 1500 bytes MTU frame with 28 bytes IP+ICMP headers
    "ping_df_bit": True,       # Don't Fragment bit enabled
}


class VersaInteractiveShell:
    """
    Manages an interactive VT100 shell session across multi-hop SSH jumps:
    [Bastion PU1PAMMCPSGW01] -> [Versa Director 10.29.11.32] -> [Branch Router CLI]
    """
    def __init__(self, log_file=None):
        self.client = None
        self.channel = None
        self.log_file = log_file
        self.full_transcript = []

    def log(self, text):
        cleaned = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)
        self.full_transcript.append(cleaned)
        if self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(cleaned)

    def connect_direct(self, hostname, username, password=None, port=22):
        if not HAS_PARAMIKO:
            raise RuntimeError("paramiko library is required. Install via: pip install paramiko")

        print(f"[*] Connecting directly to Versa Director: {hostname} (user: {username})...")
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=hostname,
            port=port,
            username=username,
            password=password if password else None,
            timeout=30,
            look_for_keys=False,
            allow_agent=False
        )
        self.channel = self.client.invoke_shell(term="vt100", width=250, height=60)
        self.channel.settimeout(3.0)
        time.sleep(1.0)
        initial_out = self._read_available()
        self.log(initial_out)
        print(f"[+] Connected directly to: {hostname}")

    def connect_jump_host(self, hostname, username, password=None, port=22):
        if not HAS_PARAMIKO:
            raise RuntimeError("paramiko library is required. Install via: pip install paramiko")

        print(f"[*] Connecting to Jump Host / Bastion: {hostname} ({username})...")
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=hostname,
            port=port,
            username=username,
            password=password if password else None,
            timeout=30,
            look_for_keys=True,
            allow_agent=True
        )
        self.channel = self.client.invoke_shell(term="vt100", width=250, height=60)
        self.channel.settimeout(3.0)
        time.sleep(1.0)
        initial_out = self._read_available()
        self.log(initial_out)
        print(f"[+] Connected to Bastion: {hostname}")

    def _read_available(self, max_wait=2.0):
        buffer = ""
        start = time.time()
        while time.time() - start < max_wait:
            if self.channel.recv_ready():
                chunk = self.channel.recv(8192).decode("utf-8", errors="replace")
                buffer += chunk
                start = time.time()  # Reset timer on new data
            else:
                time.sleep(0.1)
        return buffer

    def send_and_wait(self, command, expected_patterns=None, timeout=45, password_response=None):
        """
        Sends command to shell and waits until one of expected_patterns is found in buffer.
        Handles password prompts and pagination automatically.
        """
        if command is not None:
            self.channel.send(command + "\n")
            self.log(f"\n>>> {command}\n")

        buffer = ""
        start_time = time.time()

        if expected_patterns is None:
            expected_patterns = [r"[\$#>]"]

        while time.time() - start_time < timeout:
            if self.channel.recv_ready():
                chunk = self.channel.recv(8192).decode("utf-8", errors="replace")
                buffer += chunk
                self.log(chunk)

                # Check if password prompt appears
                if password_response and re.search(r"(?:password|Password):\s*$", buffer):
                    time.sleep(0.5)
                    self.channel.send(password_response + "\n")
                    self.log("[PASSWORD SENT]\n")
                    password_response = None  # Send once
                    continue

                # Handle pagination if any (--More--)
                if "--More--" in buffer:
                    self.channel.send(" ")
                    time.sleep(0.2)
                    continue

                # Check for expected patterns
                for pat in expected_patterns:
                    if re.search(pat, buffer):
                        time.sleep(0.3)
                        # Read any remaining bytes
                        buffer += self._read_available(max_wait=0.5)
                        return buffer
            else:
                time.sleep(0.2)

        return buffer

    def close(self):
        if self.channel:
            self.channel.close()
        if self.client:
            self.client.close()


class VersaNetworkAuditor:
    def __init__(self, config=None):
        self.config = config or DEFAULT_CONFIG
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_filename = f"router_audit_{timestamp}.log"
        self.excel_filename = f"Router_Health_Report_{timestamp}.xlsx"
        self.shell = VersaInteractiveShell(log_file=self.log_filename)
        self.audit_results = []

    def run_full_audit(self):
        print("=" * 85)
        print(" TATA COMMUNICATIONS - VERSA SD-WAN AUTOMATED ROUTER HEALTH AUDIT")
        print("=" * 85)
        print(f"Target Branch Router : {self.config['source_branch']}")
        print(f"Versa Director IP    : {self.config['director_ip']}")
        print(f"Audit Log File       : {self.log_filename}")
        print("=" * 85)

        try:
            # Step 1: Connect directly to Versa Director (10.29.11.32)
            try:
                self.shell.connect_direct(
                    hostname=self.config["director_ip"],
                    username=self.config["director_user"],
                    password=self.config["director_password"]
                )
                init_out = self.shell.send_and_wait(
                    command=None,
                    expected_patterns=[r"admin@VD-[A-Za-z0-9_-]+:~\$", r"Administrator@VD-[A-Za-z0-9_-]+>"],
                    timeout=15
                )
                if "$" in init_out:
                    print("[*] Transitioning Versa Director to CLI mode ('cli')...")
                    self.shell.send_and_wait(command="cli", expected_patterns=[r"Administrator@VD-[A-Za-z0-9_-]+>"])
                print("[+] Successfully authenticated on Versa Director CLI directly.")
            except Exception as direct_err:
                print(f"[-] Direct connection to {self.config['director_ip']} failed ({direct_err}).")
                print(f"[*] Falling back to Bastion / Jump Host ({self.config['jump_host']})...")

                self.shell.connect_jump_host(
                    hostname=self.config["jump_host"],
                    username=self.config["jump_user"],
                    password=self.config.get("jump_password")
                )
                print(f"[*] Logging into Versa Director at {self.config['director_ip']} from Bastion...")
                out = self.shell.send_and_wait(
                    command=f"ssh -l {self.config['director_user']} {self.config['director_ip']}",
                    expected_patterns=[r"admin@VD-[A-Za-z0-9_-]+:~\$", r"Administrator@VD-[A-Za-z0-9_-]+>"],
                    password_response=self.config["director_password"],
                    timeout=30
                )
                if "$" in out:
                    print("[*] Transitioning Versa Director to CLI mode ('cli')...")
                    self.shell.send_and_wait(command="cli", expected_patterns=[r"Administrator@VD-[A-Za-z0-9_-]+>"])
                print("[+] Successfully authenticated on Versa Director CLI via Bastion.")

            # Step 3: Lookup Device IP for Branch Router
            branch_name = self.config["source_branch"]
            print(f"\n[*] Querying Director device list for '{branch_name}'...")
            lookup_out = self.shell.send_and_wait(
                command=f"show devices list | match {branch_name}",
                expected_patterns=[r"Administrator@VD-[A-Za-z0-9_-]+>"]
            )

            # Parse Branch IP address
            # Expected format: 1074-KAIJSB-KOLHAPUR-R1    10.118.14.101   -   netconf   unlocked
            ip_match = re.search(r"(\b10\.\d+\.\d+\.\d+\b)", lookup_out)
            if not ip_match:
                raise RuntimeError(f"Could not resolve IP address for router '{branch_name}' from Director output:\n{lookup_out}")

            branch_ip = ip_match.group(1)
            print(f"[+] Found Management IP for {branch_name}: {branch_ip}")

            # Step 4: SSH from Director into Branch Router
            print(f"\n[*] SSHing from Versa Director to Branch Router {branch_ip}...")
            router_login = self.shell.send_and_wait(
                command=f"ssh {self.config['router_user']}@{branch_ip}",
                expected_patterns=[r"\[admin@[A-Za-z0-9_-]+:\s*~\]\s*\$", r"admin@[A-Za-z0-9_-]+-cli>"],
                password_response=self.config["router_password"],
                timeout=35
            )

            # Transition router to CLI mode if in Linux bash
            if "$" in router_login:
                print("[*] Entering Branch Router CLI ('cli')...")
                self.shell.send_and_wait(
                    command="cli",
                    expected_patterns=[r"admin@[A-Za-z0-9_-]+-cli>"]
                )

            print(f"[+] Successfully logged into Branch Router CLI: {branch_name}")

            # Step 5: Discover Transport VRF and BGP Neighbor
            print("\n[*] Inspecting BGP Neighbors to identify Transport VRF and Source IP...")
            bgp_out = self.shell.send_and_wait(
                command="show bgp neighbor brief",
                expected_patterns=[r"admin@[A-Za-z0-9_-]+-cli>"]
            )

            transport_vrf, neighbor_ip, source_ip = self.parse_bgp_info(bgp_out)
            print(f"[+] Detected Transport VRF : {transport_vrf}")
            print(f"[+] BGP Neighbor (PE IP)  : {neighbor_ip}")
            print(f"[+] Derived Local Source IP: {source_ip} (Neighbor + 1)")

            # Step 6: Benchmark each Target Site
            for site in self.config["target_sites"]:
                print("\n" + "-" * 85)
                print(f"[*] Querying SD-WAN details for destination site: {site}")
                print("-" * 85)

                site_out = self.shell.send_and_wait(
                    command=f"show orgs org MB-NET sd-wan detail {site}",
                    expected_patterns=[r"admin@[A-Za-z0-9_-]+-cli>"],
                    timeout=30
                )

                circuits = self.parse_sdwan_circuits(site_out)
                if not circuits:
                    print(f"[-] Warning: No TCL/TTSL MPLS circuits found in NAT Status for {site}")
                    continue

                for circuit_name, dest_ip in circuits.items():
                    print(f"\n[>] Auditing Circuit: {circuit_name} -> Destination IP: {dest_ip}")
                    ping_cmd = (
                        f"ping {dest_ip} source {source_ip} "
                        f"routing-instance {transport_vrf} rapid enable "
                        f"count {self.config['ping_count']} "
                        f"packet-size {self.config['ping_packet_size']} "
                        f"df-bit enable"
                    )
                    print(f"    Executing: {ping_cmd}")
                    ping_out = self.shell.send_and_wait(
                        command=ping_cmd,
                        expected_patterns=[r"admin@[A-Za-z0-9_-]+-cli>"],
                        timeout=45
                    )

                    stats = self.parse_ping_output(ping_out)
                    stats["Source Router"] = branch_name
                    stats["Destination Site"] = site
                    stats["Access Circuit"] = circuit_name
                    stats["Destination IP"] = dest_ip
                    stats["Source IP"] = source_ip
                    stats["Transport VRF"] = transport_vrf
                    stats["Tested At"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    # Evaluate SLA
                    loss_ok = stats["Packet Loss (%)"] <= self.config["sla_max_packet_loss_pct"]
                    lat_ok = stats["Avg RTT (ms)"] <= self.config["sla_max_avg_latency_ms"] if stats["Avg RTT (ms)"] is not None else False

                    if loss_ok and lat_ok:
                        stats["SLA Verdict"] = "PASS"
                    else:
                        stats["SLA Verdict"] = "FAIL"

                    self.audit_results.append(stats)
                    print(f"    Result: {stats['SLA Verdict']} | Loss: {stats['Packet Loss (%)']}% | Avg RTT: {stats['Avg RTT (ms)']} ms")

            # Step 7: Clean disconnect
            print("\n[*] Exiting CLI and closing sessions...")
            self.shell.send_and_wait("exit", expected_patterns=[r".*"], timeout=3)
            self.shell.close()

            # Step 8: Generate Reports
            self.generate_reports()

        except Exception as e:
            print(f"\n[!] Audit Error: {e}")
            if self.shell:
                self.shell.close()
            raise

    def parse_bgp_info(self, bgp_output):
        """
        Extracts Transport VRF (e.g. TCL-MPLS1-MB-Transport-VR) and calculates
        CE Source IP by adding 1 to PE Neighbor IP.
        """
        # Look for VRF ending in -Transport-VR or containing TCL/TTSL
        vrf_match = re.search(r"routing-instance:\s+([A-Za-z0-9_-]*(?:TCL|TTSL)[A-Za-z0-9_-]*Transport-VR)", bgp_output, re.IGNORECASE)
        if not vrf_match:
            # Fallback to any Transport VRF
            vrf_match = re.search(r"routing-instance:\s+([A-Za-z0-9_-]*Transport-VR)", bgp_output, re.IGNORECASE)

        transport_vrf = vrf_match.group(1).strip() if vrf_match else "TCL-MPLS1-MB-Transport-VR"

        # Find the block under this VRF
        vrf_section = bgp_output.split(f"routing-instance: {transport_vrf}")[-1]
        
        # Match IPv4 neighbor line: e.g. 10.116.197.69   4  135088 ...
        neighbor_match = re.search(r"(\b10\.\d+\.\d+\.\d+\b)\s+\d+\s+\d+", vrf_section)
        if not neighbor_match:
            raise ValueError(f"Could not locate BGP neighbor IP under VRF '{transport_vrf}'")

        neighbor_ip_str = neighbor_match.group(1)
        neighbor_ip = ipaddress.IPv4Address(neighbor_ip_str)

        # Source IP is neighbor + 1 (point-to-point /30 subnet)
        source_ip = str(neighbor_ip + 1)
        return transport_vrf, str(neighbor_ip), source_ip

    def parse_sdwan_circuits(self, sdwan_output):
        """
        Parses 'NAT Status:' table for TCL-MPLS1-MB and TCL-MPLS2-MB (or TTSL-MPLS)
        Extracts DataPath IP or Public IP using robust line token splitting.
        """
        circuits = {}
        in_nat_table = False

        for line in sdwan_output.splitlines():
            line_clean = line.strip()
            if "NAT Status:" in line:
                in_nat_table = True
                continue

            if in_nat_table:
                # Stop if an empty line or next section header begins
                if line_clean.startswith("[ok]") or line_clean.startswith("admin@"):
                    break

                tokens = line_clean.split()
                # Expected format:
                # [0] LINK ID: 5
                # [1] FAMILY : ipv4
                # [2] CIRCUIT: TCL-MPLS2-MB
                # [3] NAT    : true / false
                # [4] PUB IP : 10.116.135.22
                # [5] PUB PORT: 29479
                # [6] DP IP  : 10.116.135.22
                # [7] DP PORT: 29479
                if len(tokens) >= 7 and tokens[1].lower() == "ipv4":
                    circuit_name = tokens[2].upper()
                    # DataPath IP is tokens[6] if present, fallback to Public IP tokens[4]
                    target_ip = tokens[6] if len(tokens) >= 7 and re.match(r"^\d+\.\d+\.\d+\.\d+$", tokens[6]) else tokens[4]

                    # Filter for TCL or TTSL MPLS circuits
                    if any(key in circuit_name for key in ["TCL-MPLS", "TTSL-MPLS"]):
                        circuits[tokens[2]] = target_ip

        return circuits

    def parse_ping_output(self, ping_output):
        """
        Parses Linux/Versa ping output:
        500 packets transmitted, 500 received, 0% packet loss, time 7724ms
        rtt min/avg/max/mdev = 12.436/18.000/60.968/5.763 ms
        """
        stats = {
            "Packets Transmitted": None,
            "Packets Received": None,
            "Packet Loss (%)": 100.0,
            "Min RTT (ms)": None,
            "Avg RTT (ms)": None,
            "Max RTT (ms)": None,
            "Mdev RTT (ms)": None,
        }

        loss_match = re.search(r"(\d+)\s+packets transmitted,\s*(\d+)\s+received,\s*(\d+(?:\.\d+)?)%\s+packet loss", ping_output)
        if loss_match:
            stats["Packets Transmitted"] = int(loss_match.group(1))
            stats["Packets Received"] = int(loss_match.group(2))
            stats["Packet Loss (%)"] = float(loss_match.group(3))

        rtt_match = re.search(r"rtt min/avg/max/mdev\s*=\s*([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)\s*ms", ping_output)
        if rtt_match:
            stats["Min RTT (ms)"] = float(rtt_match.group(1))
            stats["Avg RTT (ms)"] = float(rtt_match.group(2))
            stats["Max RTT (ms)"] = float(rtt_match.group(3))
            stats["Mdev RTT (ms)"] = float(rtt_match.group(4))

        return stats

    def generate_reports(self):
        print("\n" + "=" * 85)
        print("               EXECUTIVE NETWORK AUDIT REPORT SCORECARD")
        print("=" * 85)

        if not self.audit_results:
            print("No test results recorded.")
            return

        df = pd.DataFrame(self.audit_results)

        # Print beautiful ASCII table to console
        print(df[["Destination Site", "Access Circuit", "Destination IP", "Packet Loss (%)", "Avg RTT (ms)", "SLA Verdict"]].to_string(index=False))

        # Save Excel report
        try:
            df.to_excel(self.excel_filename, index=False)
            print(f"\n[+] Detailed Excel report generated : {self.excel_filename}")
        except Exception as e:
            print(f"[-] Could not save Excel file: {e}")

        # Save CSV report
        csv_filename = self.excel_filename.replace(".xlsx", ".csv")
        try:
            df.to_csv(csv_filename, index=False)
            print(f"[+] CSV summary report generated     : {csv_filename}")
        except Exception:
            pass

        print(f"[+] Full terminal session log saved  : {self.log_filename}")
        print("=" * 85)


# ==================================================================================================
# DRY RUN / SIMULATION MODE (TESTING WITH USER'S REAL LOG DATA)
# ==================================================================================================
def run_simulation():
    """
    Simulates the audit using the user's exact manual terminal logs to demonstrate
    100% accurate parsing and report generation offline!
    """
    print("=" * 85)
    print(" [DRY RUN / SIMULATION MODE] PARSING USER'S REAL LOG DATA")
    print("=" * 85)

    auditor = VersaNetworkAuditor()

    # User's manual BGP output
    sample_bgp = """
routing-instance: MB-NET-Control-VR

Neighbor        V  MsgRcvd   MsgSent    Uptime     State/PfxRcd  PfxSent AS
10.118.0.10     4  551007    155746     6w4d22h    1192          7       64512
10.118.0.14     4  552195    155761     6w4d22h    1192          7       64512
10.118.0.38     4  560004    155686     5d17h00m   1192          7       64512
10.118.0.42     4  556764    155755     5d16h59m   1192          7       64512

routing-instance: TCL-MPLS1-MB-Transport-VR

Neighbor        V  MsgRcvd   MsgSent    Uptime     State/PfxRcd  PfxSent AS
10.116.197.69   4  135088    155294     3w0d23h    151           0       4755
    """

    vrf, neighbor, source_ip = auditor.parse_bgp_info(sample_bgp)
    print(f"[+] Transport VRF Extracted : {vrf}")
    print(f"[+] BGP Neighbor (PE) IP    : {neighbor}")
    print(f"[+] Local Source (CE) IP    : {source_ip}")

    # User's manual SD-WAN detail output for PDC Kharghar
    sample_sdwan_pdc = """
 NAT Status:

 LINK      LINK         ACCESS     NAT                                  PUBLIC    PUBLIC                                DataPath  DataPath
   ID    FAMILY        CIRCUIT  STATUS                                      IP      PORT                                      IP      PORT
------------------------------------------------------------------------------------------------------------------------------------
    3      ipv4 PGCIL-MPLS1-MB    true                           10.116.134.10     16578                           10.116.134.10     16578
    4      ipv4    VI-MPLS1-MB    true                           10.116.132.10     19857                           10.116.132.10     19857
    5      ipv4   TCL-MPLS2-MB    true                           10.116.135.22     29479                           10.116.135.22     29479
    6      ipv4  TTSL-MPLS2-MB    true                           10.116.133.22      3715                           10.116.133.22      3715
    1      ipv4   TCL-MPLS1-MB   false                           10.116.135.10      4790                           10.116.135.10      4790
    2      ipv4  TTSL-MPLS1-MB   false                           10.116.133.10      4790                           10.116.133.10      4790
    7      ipv4 PGCIL-MPLS2-MB   false                           10.116.134.22      4790                           10.116.134.22      4790
    8      ipv4    VI-MPLS2-MB   false                           10.116.132.22      4790                           10.116.132.22      4790
    """

    circuits_pdc = auditor.parse_sdwan_circuits(sample_sdwan_pdc)
    print(f"[+] PDC Kharghar Circuits Identified : {circuits_pdc}")

    # User's manual ping output for PDC Kharghar Link 1
    sample_ping1 = """
--- 10.116.135.10 ping statistics ---
500 packets transmitted, 500 received, 0% packet loss, time 7724ms
rtt min/avg/max/mdev = 12.436/18.000/60.968/5.763 ms, pipe 4, ipg/ewma 15.478/18.772 ms
    """
    stats1 = auditor.parse_ping_output(sample_ping1)
    stats1.update({
        "Source Router": "1074-KAIJSB-KOLHAPUR-R1",
        "Destination Site": "IFT-MB-3007-PDCKHR-R1",
        "Access Circuit": "TCL-MPLS1-MB",
        "Destination IP": "10.116.135.10",
        "Source IP": source_ip,
        "Transport VRF": vrf,
        "Tested At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "SLA Verdict": "PASS" if stats1["Packet Loss (%)"] == 0 and stats1["Avg RTT (ms)"] <= 50 else "FAIL"
    })
    auditor.audit_results.append(stats1)

    # User's manual ping output for PDC Kharghar Link 2
    sample_ping2 = """
--- 10.116.135.22 ping statistics ---
500 packets transmitted, 500 received, 0% packet loss, time 7819ms
rtt min/avg/max/mdev = 12.537/19.272/53.172/7.141 ms, pipe 4, ipg/ewma 15.670/16.557 ms
    """
    stats2 = auditor.parse_ping_output(sample_ping2)
    stats2.update({
        "Source Router": "1074-KAIJSB-KOLHAPUR-R1",
        "Destination Site": "IFT-MB-3007-PDCKHR-R1",
        "Access Circuit": "TCL-MPLS2-MB",
        "Destination IP": "10.116.135.22",
        "Source IP": source_ip,
        "Transport VRF": vrf,
        "Tested At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "SLA Verdict": "PASS" if stats2["Packet Loss (%)"] == 0 and stats2["Avg RTT (ms)"] <= 50 else "FAIL"
    })
    auditor.audit_results.append(stats2)

    # User's manual ping output for CCILDD Link 1
    sample_ping3 = """
--- 10.116.192.66 ping statistics ---
500 packets transmitted, 500 received, 0% packet loss, time 7402ms
rtt min/avg/max/mdev = 11.536/16.635/56.449/5.472 ms, pipe 4, ipg/ewma 14.835/18.902 ms
    """
    stats3 = auditor.parse_ping_output(sample_ping3)
    stats3.update({
        "Source Router": "1074-KAIJSB-KOLHAPUR-R1",
        "Destination Site": "IFT-MB-3038-CCILDD-R1",
        "Access Circuit": "TCL-MPLS1-MB",
        "Destination IP": "10.116.192.66",
        "Source IP": source_ip,
        "Transport VRF": vrf,
        "Tested At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "SLA Verdict": "PASS" if stats3["Packet Loss (%)"] == 0 and stats3["Avg RTT (ms)"] <= 50 else "FAIL"
    })
    auditor.audit_results.append(stats3)

    # User's manual ping output for CCILDD Link 2
    sample_ping4 = """
--- 10.116.192.154 ping statistics ---
500 packets transmitted, 500 received, 0% packet loss, time 8150ms
rtt min/avg/max/mdev = 13.292/20.811/88.362/8.973 ms, pipe 6, ipg/ewma 16.332/19.529 ms
    """
    stats4 = auditor.parse_ping_output(sample_ping4)
    stats4.update({
        "Source Router": "1074-KAIJSB-KOLHAPUR-R1",
        "Destination Site": "IFT-MB-3038-CCILDD-R1",
        "Access Circuit": "TCL-MPLS2-MB",
        "Destination IP": "10.116.192.154",
        "Source IP": source_ip,
        "Transport VRF": vrf,
        "Tested At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "SLA Verdict": "PASS" if stats4["Packet Loss (%)"] == 0 and stats4["Avg RTT (ms)"] <= 50 else "FAIL"
    })
    auditor.audit_results.append(stats4)

    auditor.generate_reports()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["--simulate", "--dry-run", "-s"]:
        run_simulation()
    else:
        # Prompt for credentials if not hardcoded
        if not DEFAULT_CONFIG["director_password"]:
            import getpass
            print("--- Credential Setup ---")
            p_director = getpass.getpass("Enter password for Versa Director (admin@10.29.11.32): ")
            DEFAULT_CONFIG["director_password"] = p_director
            p_router = getpass.getpass("Enter password for Branch Router (admin) [press Enter if same]: ")
            DEFAULT_CONFIG["router_password"] = p_router if p_router else p_director

        auditor = VersaNetworkAuditor(DEFAULT_CONFIG)
        auditor.run_full_audit()
