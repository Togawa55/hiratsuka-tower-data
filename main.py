from __future__ import annotations

import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import Locator, Page, sync_playwright

URL = "https://www.hiratsuka-tower.jp/list"
JST = ZoneInfo("Asia/Tokyo")
DATA_DIR = Path("data")
CSV_PATH = DATA_DIR / "hiratsuka_tower.csv"
DEBUG_DIR = Path("debug")

FIELDS = ["date", "time", "wave_height", "wave_period", "wind_speed", "wind_direction", "collected_at"]
DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def find_index(headers: list[str], candidates: list[str]) -> int | None:
    for i, header in enumerate(headers):
        normalized = clean(header)
        if any(candidate in normalized for candidate in candidates):
            return i
    return None


def extract_rows(html: str, data_date: str, collected_at: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        raw_rows = []
        for tr in table.find_all("tr"):
            cells = [clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            if cells:
                raw_rows.append(cells)

        if len(raw_rows) < 2:
            continue

        # Header may span one or two rows. Combine the first two rows when useful.
        header_candidates = [raw_rows[0]]
        if len(raw_rows) >= 3:
            width = max(len(raw_rows[0]), len(raw_rows[1]))
            combined = []
            for i in range(width):
                a = raw_rows[0][i] if i < len(raw_rows[0]) else ""
                b = raw_rows[1][i] if i < len(raw_rows[1]) else ""
                combined.append(clean(a + b))
            header_candidates.append(combined)

        for headers in header_candidates:
            time_i = find_index(headers, ["時刻", "時間", "時"])
            wave_height_i = find_index(headers, ["波高"])
            wave_period_i = find_index(headers, ["波周期", "周期"])
            wind_speed_i = find_index(headers, ["風速"])
            wind_direction_i = find_index(headers, ["風向"])

            required = [time_i, wave_height_i, wave_period_i, wind_speed_i, wind_direction_i]
            if any(i is None for i in required):
                continue

            start_row = 1 if headers is raw_rows[0] else 2
            output = []
            for cells in raw_rows[start_row:]:
                max_i = max(i for i in required if i is not None)
                if len(cells) <= max_i:
                    continue
                time_text = cells[time_i]  # type: ignore[index]
                match = re.search(r"(?:^|\D)([01]?\d|2[0-3])(?::00|時)?(?:\D|$)", time_text)
                if not match:
                    continue
                hour = int(match.group(1))
                output.append(
                    {
                        "date": data_date,
                        "time": f"{hour:02d}:00",
                        "wave_height": cells[wave_height_i],  # type: ignore[index]
                        "wave_period": cells[wave_period_i],  # type: ignore[index]
                        "wind_speed": cells[wind_speed_i],  # type: ignore[index]
                        "wind_direction": cells[wind_direction_i],  # type: ignore[index]
                        "collected_at": collected_at,
                    }
                )
            if output:
                return output

    raise RuntimeError("波高・波周期・風速・風向を含む表を見つけられませんでした。")


def save_csv(new_rows: list[dict[str, str]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[tuple[str, str], dict[str, str]] = {}

    if CSV_PATH.exists():
        with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                existing[(row["date"], row["time"])] = row

    for row in new_rows:
        # Do not replace a good existing value with a completely blank row.
        values = [row["wave_height"], row["wave_period"], row["wind_speed"], row["wind_direction"]]
        if any(values):
            existing[(row["date"], row["time"])] = row

    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for key in sorted(existing):
            writer.writerow(existing[key])


def visible_date_locator(page: Page) -> Locator:
    candidates = page.get_by_text(DATE_RE).all()
    for locator in candidates:
        if locator.is_visible():
            return locator
    raise RuntimeError("画面上の日付表示を見つけられませんでした。")


def read_displayed_date(page: Page) -> date:
    text = visible_date_locator(page).inner_text()
    match = DATE_RE.search(text)
    if not match:
        raise RuntimeError(f"日付を読み取れませんでした: {text}")
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def click_date_arrow(page: Page, direction: str) -> None:
    """Click the circular arrow button immediately left/right of the displayed date."""
    date_locator = visible_date_locator(page)
    date_box = date_locator.bounding_box()
    if not date_box:
        raise RuntimeError("日付表示の位置を取得できませんでした。")

    date_cx = date_box["x"] + date_box["width"] / 2
    date_cy = date_box["y"] + date_box["height"] / 2

    candidates: list[tuple[float, Locator]] = []
    for button in page.locator("button").all():
        if not button.is_visible():
            continue
        box = button.bounding_box()
        if not box:
            continue
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2

        # Date arrows are near the same horizontal row as the date text.
        if abs(cy - date_cy) > 100:
            continue

        if direction == "previous" and cx < date_cx:
            candidates.append((date_cx - cx, button))
        elif direction == "next" and cx > date_cx:
            candidates.append((cx - date_cx, button))

    if not candidates:
        raise RuntimeError(f"{direction} の日付移動ボタンを見つけられませんでした。")

    # The nearest button to the date is the previous/next date button.
    _, target_button = min(candidates, key=lambda item: item[0])
    before = read_displayed_date(page)
    target_button.click()

    # Wait until the displayed date actually changes.
    page.wait_for_function(
        """([pattern, beforeText]) => {
            const re = new RegExp(pattern);
            return Array.from(document.querySelectorAll('body *')).some(el => {
                const text = (el.textContent || '').trim();
                return re.test(text) && text !== beforeText;
            });
        }""",
        arg=[r"\\d{4}年\\d{1,2}月\\d{1,2}日", f"{before.year}年{before.month}月{before.day}日"],
        timeout=30_000,
    )
    page.wait_for_timeout(2_000)


def navigate_to_date(page: Page, target: date) -> None:
    """Move the list page to target date using the on-page < / > buttons."""
    for _ in range(10):
        current = read_displayed_date(page)
        if current == target:
            return
        if current > target:
            click_date_arrow(page, "previous")
        else:
            click_date_arrow(page, "next")

    raise RuntimeError(f"目的の日付 {target.isoformat()} に移動できませんでした。")


def main() -> None:
    now = datetime.now(JST)
    target_date = now.date() - timedelta(days=1)
    data_date = target_date.strftime("%Y-%m-%d")
    collected_at = now.isoformat(timespec="seconds")
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="ja-JP", timezone_id="Asia/Tokyo")
        page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(8_000)

        displayed = read_displayed_date(page)
        print(f"最初に表示された日付: {displayed.isoformat()}")
        print(f"取得対象日: {data_date}")

        navigate_to_date(page, target_date)
        final_displayed = read_displayed_date(page)
        print(f"取得するページの日付: {final_displayed.isoformat()}")

        html = page.content()
        (DEBUG_DIR / "latest.html").write_text(html, encoding="utf-8")
        page.screenshot(path=str(DEBUG_DIR / "latest.png"), full_page=True)
        browser.close()

    rows = extract_rows(html, data_date, collected_at)
    save_csv(rows)
    print(f"{data_date} の {len(rows)} 行を読み取り、{CSV_PATH} を更新しました。")


if __name__ == "__main__":
    main()
