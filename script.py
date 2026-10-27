from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import time

CHATBOT_URL = "https://chatbot.hellotars.com/conv/MfRHci"

# Specific options that confirm the circuit is PUNCHABLE
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

CIDS = [
    "20080101593",
    "79060072146",
    "22060076670",
    "22060076693"
]


def check_cid(cid):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # 1. Open chatbot
            page.goto(CHATBOT_URL, wait_until="domcontentloaded", timeout=50000)
            page.wait_for_timeout(2500)

            # Helper for safe clicking respecting HelloTars 1000ms delay
            def safe_click(text):
                loc = page.get_by_text(text, exact=False).first
                loc.wait_for(state="visible", timeout=25000)
                page.wait_for_timeout(1200)
                loc.click()
                page.wait_for_timeout(1800)

            # 2-5: Navigate conversation tree
            safe_click("Get Customer Support")
            safe_click("Connectivity Products")
            safe_click("Raise Service Request/Complaint")
            safe_click("Technical Issue")

            # 6: Input CID
            box = page.locator('input[placeholder="Type your message..."]')
            box.wait_for(state="visible", timeout=20000)
            page.wait_for_timeout(1200)
            box.fill(cid)
            page.wait_for_timeout(500)
            page.keyboard.press("Enter")

            try:
                send_btn = page.locator('button:has(svg), svg[data-icon="paper-plane"]').first
                if send_btn.is_visible():
                    send_btn.click()
            except Exception:
                pass

            # 7: Poll up to 20 seconds for final response
            status = "Non-Punchable"
            start_time = time.time()
            while time.time() - start_time < 20:
                page.wait_for_timeout(1000)
                body = page.locator("body").inner_text()

                if any(opt.lower() in body.lower() for opt in PUNCHABLE_OPTIONS):
                    status = "Punchable"
                    break

                if any(inv.lower() in body.lower() for inv in INVALID_KEYWORDS):
                    status = "Non-Punchable"
                    break

            browser.close()
            print(f"{cid} -> {status}")
            return {"CID": cid, "Status": status}

    except Exception as e:
        print(f"{cid} -> ERROR: {e}")
        return {"CID": cid, "Status": f"Error: {str(e)[:50]}"}


if __name__ == "__main__":
    results = []
    MAX_WORKERS = 5

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(check_cid, cid) for cid in CIDS]
        for future in as_completed(futures):
            results.append(future.result())

    df = pd.DataFrame(results)
    df.to_excel("CID_Result.xlsx", index=False)
    print("\nExcel generated: CID_Result.xlsx")
    print(df)