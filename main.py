import csv
import pandas as pd
import datetime
import os
import time
import threading
import random
import re
import json
import sys
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound

from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# ================= CONFIG =================
processed = 0
total = 0
lock = threading.Lock()
csv_lock = threading.Lock()

SPREADSHEET_ID = "1A2KK8bQaJukV9R7FHdOvnmZVQMk2b0IE2971ZBB-Hgs"
TARGET_SHEET = "upload"

# ================= DRIVER =================
def create_driver(driver_path):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    # 🚀 tăng tốc
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    options.page_load_strategy = "eager"

    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(20)

    return driver

# ================= SCRAPE FAST =================
def scrape_fast(driver, ma_kh, max_retry=3):
    thoi_gian = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for attempt in range(max_retry):
        try:
            input_el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "idMaKhachHang"))
            )

            input_el.clear()
            input_el.send_keys(ma_kh)
            input_el.send_keys(Keys.RETURN)

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "idThongTinLichNgungGiamMaKhachHang"))
            )

            time.sleep(1)

            content = driver.find_element(
                By.ID, "idThongTinLichNgungGiamMaKhachHang"
            ).text.strip()

            if content:
                return {
                    "Ma_KH": ma_kh,
                    "Thoi_gian": thoi_gian,
                    "Noi_dung": content
                }

        except Exception:
            print(f"\n🔁 Retry {attempt+1} | {ma_kh}")
            try:
                driver.refresh()
            except:
                pass
            time.sleep(2)

    return {
        "Ma_KH": ma_kh,
        "Thoi_gian": thoi_gian,
        "Noi_dung": "Lỗi sau retry"
    }

# ================= WORKER =================
def worker(data, driver_path, output):
    global processed
    driver = create_driver(driver_path)
    buffer = []

    try:
        # 🚀 load 1 lần
        driver.get("https://cskh.evnspc.vn/TraCuu/LichNgungGiamCungCapDien")

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "idMaKhachHang"))
        )

        for ma_kh in data:
            res = scrape_fast(driver, ma_kh)
            buffer.append(res)

            with lock:
                processed += 1
                percent = (processed / total) * 100
                bar = "█" * int(percent // 2) + "-" * (50 - int(percent // 2))
                print(f"\r📊 [{bar}] {processed}/{total} ({percent:.1f}%)", end="", flush=True)

            if len(buffer) >= 5:
                write_csv(output, buffer)
                buffer = []

            time.sleep(random.uniform(0.7, 1.2))

        if buffer:
            write_csv(output, buffer)

    finally:
        driver.quit()

# ================= CSV =================
def write_csv(file, rows, mode='a', header=False):
    with csv_lock:
        with open(file, mode, newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=["Ma_KH", "Thoi_gian", "Noi_dung"])
            if header:
                writer.writeheader()
            writer.writerows(rows)

# ================= PROCESS =================
def process(input_csv):
    print("\n🧹 Đang xử lý dữ liệu...")
    df = pd.read_csv(input_csv)
    rows = []

    for _, row in df.iterrows():
        text = str(row["Noi_dung"])

        kh = re.search(r"KHÁCH HÀNG:\s*(.+)", text)
        dc = re.search(r"ĐỊA CHỈ:\s*(.+)", text)

        blocks = re.split(r"(?=MÃ.*?LỊCH)", text, flags=re.IGNORECASE)

        for b in blocks:
            ma = re.search(r"MÃ.*LỊCH:\s*(\d+)", b)
            tg = re.search(r"từ (.+?) ngày (.+?) đến (.+?) ngày (.+)", b)
            lydo = re.search(r"LÝ DO.*:\s*(.+)", b)

            if ma and tg:
                rows.append([
                    row["Ma_KH"],
                    kh.group(1) if kh else "",
                    dc.group(1) if dc else "",
                    ma.group(1),
                    tg.group(2), tg.group(1),
                    tg.group(4), tg.group(3),
                    lydo.group(1) if lydo else ""
                ])

    df2 = pd.DataFrame(rows, columns=[
        "Ma_KH", "Khach_hang", "Dia_chi",
        "Ma_lich", "Ngay_BD", "Gio_BD",
        "Ngay_KT", "Gio_KT", "Ly_do"
    ])

    df2.to_excel("output.xlsx", index=False)
    print("📁 Đã xuất output.xlsx")

    return df2

# ================= RETRY =================
def retry(func, max_retries=5):
    for i in range(max_retries):
        try:
            return func()
        except Exception as e:
            print(f"⚠️ Retry {i+1}: {e}")
            time.sleep((2 ** i) + random.uniform(0, 2))
    raise Exception("Fail sau nhiều retry")

# ================= GOOGLE SHEETS =================
def upload_sheet(df):
    try:
        raw = os.getenv("GCP_JSON")
        if not raw:
            print("⚠️ Thiếu GCP_JSON")
            return

        raw = raw.replace("\\\\n", "\\n")
        info = json.loads(raw)
        info["private_key"] = info["private_key"].replace("\\n", "\n")

        creds = Credentials.from_service_account_info(info, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])

        client = gspread.authorize(creds)

        sheet = retry(lambda: client.open_by_key(SPREADSHEET_ID))

        try:
            ws = retry(lambda: sheet.worksheet(TARGET_SHEET))
        except WorksheetNotFound:
            ws = retry(lambda: sheet.add_worksheet(title=TARGET_SHEET, rows="1000", cols="20"))

        data = [df.columns.tolist()] + df.astype(str).values.tolist()

        def do_update():
            ws.clear()
            time.sleep(1)
            ws.update(range_name="A1", values=data)

        retry(do_update)

        print("\n✅ Upload Google Sheets thành công!")

    except Exception as e:
        print("❌ Lỗi Google Sheets:", e)

# ================= MAIN =================
if __name__ == "__main__":
    file_input = "makh_list.csv"
    file_raw = "raw.csv"

    if not os.path.exists(file_input):
        print("❌ Thiếu file makh_list.csv")
        sys.exit()

    with open(file_input, encoding="utf-8") as f:
        data = [r[0] for r in csv.reader(f) if r]

    total = len(data)
    print(f"🚀 Tổng {total} mã KH")

    driver_path = ChromeDriverManager().install()

    write_csv(file_raw, [], mode="w", header=True)

    threads = 3
    chunks = [data[i::threads] for i in range(threads)]

    start = time.time()

    with ThreadPoolExecutor(max_workers=threads) as ex:
        futures = [ex.submit(worker, c, driver_path, file_raw) for c in chunks]
        for f in as_completed(futures):
            f.result()

    print("\n⏳ Đợi ghi file...")
    time.sleep(2)

    df = process(file_raw)

    print("⏳ Upload sau 3s...")
    time.sleep(3)

    upload_sheet(df)

    print(f"🏁 Xong trong {round(time.time()-start,2)}s")
