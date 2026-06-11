"""Browser-driven verification of Eye Spy Grant Scout. Not part of the app."""
import json
import sys
import time
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5000"
SHOTS = r"C:\Users\ethan\Downloads\EyeSpy\Candid_dupe_EyeSpy\verify_shots"
import os
os.makedirs(SHOTS, exist_ok=True)

results = []

def step(label, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    results.append((mark, label, detail))
    print(f"[{mark}] {label} :: {detail}")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})

    # ---- 1. Dashboard ----
    page.goto(BASE)
    step("Dashboard loads", page.title().startswith("Dashboard"), page.title())
    stats = page.locator(".stat .num").all_inner_texts()
    step("Dashboard stats render", len(stats) == 3 and stats[0] != "0", f"stats={stats}")
    vision_badge = page.locator("h2 .badge-vision").first.inner_text()
    step("Vision grants section shows count", "found" in vision_badge, vision_badge)
    page.screenshot(path=f"{SHOTS}/01_dashboard.png", full_page=True)

    # ---- 2. Find Funders: real search via the form ----
    page.click("nav >> text=Find Funders")
    page.fill("#q", "blind")
    page.select_option("#state", "FL")
    page.click("button:has-text('Search')")
    page.wait_for_selector("table tbody tr")
    nrows = page.locator("table tbody tr").count()
    first_org = page.locator("table tbody tr td a strong").first.inner_text()
    step("Search 'blind' in FL returns results", nrows > 5, f"{nrows} rows, first={first_org!r}")
    page.screenshot(path=f"{SHOTS}/02_search.png", full_page=False)

    # ---- 3. Add a search result to pipeline (button click) ----
    row = page.locator("table tbody tr").first
    org_name = row.locator("td a strong").inner_text()
    row.locator("button:has-text('+ Pipeline')").click()
    page.wait_for_selector(".flash")
    flash = page.locator(".flash").inner_text()
    step("'+ Pipeline' adds and flashes", "Added" in flash, flash.strip())
    badge = page.locator("table tbody tr").first.locator(".badge").inner_text()
    step("Result row now shows 'In pipeline'", "In pipeline" in badge, badge)

    # ---- 4. Org profile via link click ----
    page.locator("table tbody tr td a strong").first.click()
    page.wait_for_selector("h1")
    h1 = page.locator("h1").inner_text()
    step("Org profile opens from search result", len(h1) > 3, h1)
    page.screenshot(path=f"{SHOTS}/03_org_profile.png", full_page=True)

    # ---- 5. Known funder profile: financials + grants table ----
    page.goto(f"{BASE}/org/596368632")
    fin_rows = page.locator("table").first.locator("tbody tr").count()
    step("duPont Fund shows financial history", fin_rows >= 3, f"{fin_rows} fiscal years")
    grants_hdr = page.locator("h2:has-text('Grants made')").inner_text()
    step("duPont Fund grants table present", "shown" in grants_hdr, grants_hdr)
    has_people = page.locator("h2:has-text('Key people')").count() == 1
    step("Key people section present", has_people, "")
    page.screenshot(path=f"{SHOTS}/04_funder_grants.png", full_page=False)

    # ---- 6. Grants Database: keyword + preset ----
    page.click("nav >> text=Grants Database")
    page.fill("#q", "blind")
    page.click("button:has-text('Search')")
    page.wait_for_selector("table tbody tr, p.muted")
    shown = page.locator("p[aria-live=polite]").inner_text() if page.locator("p[aria-live=polite]").count() else "0"
    step("Grants search 'blind' returns rows", "grant(s) shown" in shown, shown)
    page.click("text=Vision & blindness grants")
    page.wait_for_selector("table tbody tr")
    vrows = page.locator("table tbody tr").count()
    vbadges = page.locator("tbody .badge-vision").count()
    step("Vision preset filters correctly", vrows > 10 and vbadges == vrows,
         f"{vrows} rows, {vbadges} vision badges")
    page.screenshot(path=f"{SHOTS}/05_grants_vision.png", full_page=False)

    # ---- 7. Pipeline: verify added prospect, edit, save, delete ----
    page.click("nav >> text=My Pipeline")
    card = page.locator(f".card:has(h2:has-text('{org_name}'))")
    step("Prospect from search appears in pipeline", card.count() == 1, org_name)
    card.locator("select").select_option("Applied")
    card.locator("textarea").fill("verification note")
    card.locator("button:has-text('Save')").click()
    page.wait_for_selector(".flash")
    badge_txt = page.locator(f".card:has(h2:has-text('{org_name}')) h2 .badge").first.inner_text()
    step("Status edit persists after save", badge_txt == "Applied", badge_txt)
    page.screenshot(path=f"{SHOTS}/06_pipeline.png", full_page=False)
    page.on("dialog", lambda d: d.accept())
    page.locator(f".card:has(h2:has-text('{org_name}'))").locator("button:has-text('Remove')").click()
    page.wait_for_selector(".flash")
    step("Prospect delete works (with confirm dialog)",
         page.locator(f".card h2:has-text('{org_name}')").count() == 0,
         page.locator(".flash").inner_text().strip())

    # ---- 8. Indexer: JS status polling + live index of a new funder ----
    page.click("nav >> text=Indexer")
    page.wait_for_function("!document.getElementById('indexer-status').innerText.includes('Loading')",
                           timeout=15000)
    status_txt = page.locator("#indexer-status").inner_text()
    step("Indexer status box populates via JS polling", "Idle" in status_txt or "Working" in status_txt,
         status_txt.split(chr(10))[0])
    # queue Lucy Gooding (not yet indexed) via the seed table button
    page.locator("tr:has-text('Lucy Gooding') button:has-text('Index')").click()
    page.wait_for_selector(".flash")
    deadline = time.time() + 120
    done = False
    while time.time() < deadline:
        s = json.loads(page.evaluate("fetch('/indexer/status').then(r=>r.text())"))
        if any(d["ein"] == "592891582" for d in s["done"]):
            done = True
            rec = [d for d in s["done"] if d["ein"] == "592891582"][0]
            break
        if any(e["ein"] == "592891582" for e in s["errors"]):
            rec = [e for e in s["errors"] if e["ein"] == "592891582"][0]
            break
        time.sleep(3)
    step("Live indexing of Lucy Gooding Foundation completes", done,
         str(rec) if 'rec' in dir() else "timeout")
    page.reload()
    page.wait_for_function("!document.getElementById('indexer-status').innerText.includes('Loading')",
                           timeout=15000)
    page.screenshot(path=f"{SHOTS}/07_indexer.png", full_page=False)
    # her grants should now be searchable
    page.goto(f"{BASE}/org/592891582")
    gh = page.locator("h2:has-text('Grants made')").inner_text()
    step("Newly indexed funder shows grants on profile", "shown" in gh, gh)

    # ---- 9. Accessibility: large-text toggle persists ----
    page.goto(BASE)
    page.click("#font-toggle")
    big1 = page.evaluate("document.documentElement.classList.contains('big-text')")
    page.reload()
    big2 = page.evaluate("document.documentElement.classList.contains('big-text')")
    pressed = page.get_attribute("#font-toggle", "aria-pressed")
    step("Large-text toggle applies and persists across reload",
         big1 and big2 and pressed == "true", f"applied={big1} persisted={big2} aria-pressed={pressed}")
    page.screenshot(path=f"{SHOTS}/08_large_text.png", full_page=False)
    page.click("#font-toggle")  # restore

    # ---- 10. PROBES ----
    # bad EIN on org page
    page.goto(f"{BASE}/org/000000001")
    err = page.locator(".error").count()
    step("PROBE: unknown EIN shows friendly error (no crash)", err == 1,
         page.locator(".error").inner_text() if err else "no error element")
    # garbage EIN into indexer form
    page.goto(f"{BASE}/indexer")
    page.fill("#ix-ein", "not-a-number")
    page.click("button:has-text('Queue for indexing')")
    page.wait_for_selector(".flash")
    step("PROBE: garbage EIN in indexer rejected with message",
         "valid EIN" in page.locator(".flash").inner_text(), page.locator(".flash").inner_text().strip())
    # search with no results
    page.goto(f"{BASE}/search?q=zzzzqqqqxxxx")
    body = page.locator("main").inner_text()
    step("PROBE: zero-result search handled", "0" in body and "No organizations matched" in body, "")
    # nonexistent route
    r = page.request.get(f"{BASE}/nonexistent")
    step("PROBE: unknown route returns 404", r.status == 404, str(r.status))
    # grants search with weird params
    page.goto(f"{BASE}/grants?q=%27%3B--&year=abc&min_amount=-5")
    crashed = "Internal Server Error" in page.content()
    step("PROBE: malformed grants params (SQLi chars, bad year) no crash", not crashed,
         "page rendered" if not crashed else "500!")
    # double pipeline add same EIN
    page.goto(f"{BASE}/org/596368632")
    if page.locator("button:has-text('+ Add to pipeline')").count():
        page.click("button:has-text('+ Add to pipeline')")
        page.wait_for_selector(".flash")
    page.goto(f"{BASE}/search?q=jessie+ball+dupont")
    dup_guard = page.locator("tr:has-text('Religious') .badge:has-text('In pipeline')").count() >= 1
    step("PROBE: duplicate pipeline add guarded (shows In pipeline)", dup_guard, "")
    # cleanup the test prospect
    page.goto(f"{BASE}/pipeline")
    c = page.locator(".card:has(h2:has-text('Dupont'))")
    if c.count():
        c.locator("button:has-text('Remove')").click()

    browser.close()

fails = [r for r in results if r[0] == "FAIL"]
print(f"\n=== {len(results) - len(fails)}/{len(results)} steps passed ===")
sys.exit(1 if fails else 0)
