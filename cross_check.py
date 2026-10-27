import sys
import time
import re
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor, as_completed

CHATBOT_URL = "https://chatbot.hellotars.com/conv/MfRHci"

# The exact options from the Smart Assist screenshot that confirm PUNCHABLE
PUNCHABLE_OPTIONS = [
    "Please select from the options provided below",
    "Link Down",
    "Link Flapping",
    "Slow Response",
    "Application Related",
    "Packet Loss on Link",
    "High Latency",
    "MRTG Graph not Working",
    "Hardware Related",
    "Testing Request",
    "RFO Related",
    "Choose from above"
]

# Keywords that confirm the CID is INVALID (NON-PUNCHABLE)
INVALID_KEYWORDS = [
    "Invalid",
    "invalid",
    "not valid",
    "does not exist",
    "not found",
    "not registered",
    "unable to find",
    "re-enter",
    "incorrect"
]

# Sample CIDs to test (replace or pass via input)
SAMPLE_CIDS = [
    "20080101593",  # Punchable CID from screenshot
    "79060072146",
    "22060076670",
    "22060076693"
]


def check_single_run(cid, run_number):
    """
    Executes a single check on the chatbot and returns (verdict, matched_keyword).
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # 1. Open Chatbot
            page.goto(CHATBOT_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)

            # Helper for reliable clicks respecting HelloTars 1000ms delay
            def safe_click(text):
                loc = page.get_by_text(text, exact=False).first
                loc.wait_for(state="visible", timeout=25000)
                page.wait_for_timeout(1100)
                loc.click()
                page.wait_for_timeout(1800)

            # Steps 2-5: Navigate conversation tree
            safe_click("Get Customer Support")
            safe_click("Connectivity Products")
            safe_click("Raise Service Request/Complaint")
            safe_click("Technical Issue")

            # Step 6: Input CID
            box = page.locator('input[placeholder="Type your message..."]')
            box.wait_for(state="visible", timeout=20000)
            page.wait_for_timeout(1100)
            box.fill(cid)
            page.wait_for_timeout(400)
            page.keyboard.press("Enter")

            # Try clicking send button icon if visible
            try:
                send_btn = page.locator('button:has(svg), svg[data-icon="paper-plane"]').first
                if send_btn.is_visible():
                    send_btn.click()
            except Exception:
                pass

            # Step 7: Poll up to 20 seconds for response
            verdict = "Unknown"
            reason = "No response"
            start_time = time.time()

            while time.time() - start_time < 20:
                page.wait_for_timeout(1000)
                body = page.locator("body").inner_text()

                # Check for Punchable options
                matched_opts = [opt for opt in PUNCHABLE_OPTIONS if opt.lower() in body.lower()]
                if matched_opts:
                    verdict = "Punchable"
                    reason = f"Detected options: {', '.join(matched_opts[:3])}"
                    break

                # Check for Invalid / Non-punchable
                matched_inv = [inv for inv in INVALID_KEYWORDS if inv.lower() in body.lower()]
                if matched_inv:
                    verdict = "Non-Punchable"
                    reason = f"Detected: {matched_inv[0]}"
                    break

            browser.close()

            if verdict == "Unknown":
                verdict = "Error: Timeout"
                reason = "Chatbot did not respond within 20s"

            return verdict, reason

    except Exception as e:
        err = str(e).splitlines()[0] if str(e) else "Error"
        return "Error", err[:50]


def cross_check_cid(cid):
    """
    Runs the check TWICE independently for the same CID and compares results.
    """
    print(f"\n[+] Testing CID: {cid}")

    # Run 1
    v1, r1 = check_single_run(cid, run_number=1)
    print(f"    ├─ Run 1: {v1} ({r1})")

    # Brief pause between runs
    time.sleep(1.5)

    # Run 2
    v2, r2 = check_single_run(cid, run_number=2)
    print(f"    ├─ Run 2: {v2} ({r2})")

    # Compare
    if v1 == v2 and "Error" not in v1:
        match_status = "MATCH"
        final_verdict = v1
        print(f"    └─ VERDICT: [ {final_verdict} ] (100% Verified Match)")
    elif "Error" in v1 or "Error" in v2:
        match_status = "ERROR / TIMEOUT"
        final_verdict = v1 if "Error" not in v1 else v2
        print(f"    └─ VERDICT: Inconclusive (Encountered Network Timeout)")
    else:
        match_status = "MISMATCH"
        final_verdict = "Needs Review"
        print(f"    └─ VERDICT: [ MISMATCH ] Run 1={v1} vs Run 2={v2}")

    return {
        "CID": cid,
        "Run 1 Result": v1,
        "Run 1 Details": r1,
        "Run 2 Result": v2,
        "Run 2 Details": r2,
        "Comparison": match_status,
        "Final Verdict": final_verdict,
        "Checked At": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def main():
    print("=" * 65)
    print(" TTBS CID Double-Check & Cross-Verification Tool")
    print(" Designed and Developed by Tata Communications")
    print("=" * 65)

    # Check if user passed CIDs in a text file or use sample
    cids_to_check = SAMPLE_CIDS

    print(f"\nChecking {len(cids_to_check)} CIDs with 2x Cross-Verification...")
    results = []

    # Process CIDs (Max 3 workers to keep HelloTars server stable and accurate)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(cross_check_cid, cid): cid for cid in cids_to_check}
        for future in as_completed(futures):
            results.append(future.result())

    # Export comparison to Excel
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"CID_CrossCheck_Comparison_{timestamp}.xlsx"

    df = pd.DataFrame(results)
    df.to_excel(filename, index=False)

    print("\n" + "=" * 65)
    print(" CROSS-CHECK SUMMARY REPORT")
    print("=" * 65)
    matches = sum(1 for r in results if r["Comparison"] == "MATCH")
    mismatches = sum(1 for r in results if r["Comparison"] == "MISMATCH")
    errors = sum(1 for r in results if "ERROR" in r["Comparison"])

    print(f"Total CIDs Tested : {len(results)}")
    print(f"Verified Matches  : {matches} (Run 1 and Run 2 gave identical results)")
    print(f"Mismatches        : {mismatches}")
    print(f"Errors / Timeouts : {errors}")
    print(f"\nComparison Report Exported to: {filename}")
    print("=" * 65)


if __name__ == "__main__":
    main()
