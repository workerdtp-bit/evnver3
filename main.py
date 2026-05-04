import csv
import pandas as pd
import datetime
import os
import time
import threading
import random
import re
import json
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound

from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# ================= CONFIG =================
processed = 0
total = 0
skip_count = 0
lock = threading.Lock()
csv_lock = threading.Lock()
error_list = []

SPREADSHEET_ID = "1A2KK8bQaJukV9R7FHdOvnmZVQMk2b0IE2971ZBB-Hgs"
TARGET_SHEET = "upload"

# ================= DRIVER =================
def create_driver(driver_path):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    options.page_load_strategy = "eager"

    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(20)
    return driver

# ================= SCRAPE =================
def scrape_fast(driver, ma_kh, max_retry=3):
    thoi_gian = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for attempt in range(max_retry):
        try:
            input_el = WebDriverWait(driver, 10).until(
                lambda d: d.find_element(By.ID, "idMaKhachHang")
            )

            input_el.clear()
            time.sleep(0.3)
            input_el.send_keys(ma_kh)
            input_el.send_keys(Keys.RETURN)

            # Đợi TEXT thật sự
            WebDriverWait(driver, 15).until(
                lambda d: d.find_element(
                    By.ID, "idThongTinLichNgungGiamMaKhachHang"
                ).text.strip() != ""
            )

            content = driver.find_element(
                By.ID, "idThongTinLichNgungGiamMaKhachHang"
            ).text.strip()

            if "Không có lịch" in content:
                return {
                    "Ma_KH": ma_kh,
                    "Thoi_gian": thoi_gian,
                    "Noi_dung": "Không có lịch"
                }

            if len(content) < 20:
                raise Exception("Text rỗng")

            return {
                "Ma_KH": ma_kh,
                "Thoi_gian": thoi_gian,
                "Noi_dung": content
            }

        except Exception as e:
            print(f"\n🔁 Retry {attempt+1} | {ma_kh} | {e}")
            time.sleep(2)

    error_list.append(ma_kh)

    return {
        "Ma_KH": ma_kh,
        "Thoi_gian": thoi_gian,
        "Noi_dung": "Lỗi - không lấy được dữ liệu"
    }

# ================= WORKER =================
def worker(data, driver_path, output):
    global processed, skip_count
    driver = create_driver(driver_path)
    buffer = []

    try:
        driver.get("https://cskh.evnspc.vn/TraCuu/LichNgungGiamCungCapDien")

        WebDriverWait(driver, 20).until(
            lambda d: d.find_element(By.ID, "idMaKhachHang")
        )

        for ma_kh in data:
            res = scrape_fast(driver, ma_kh)

            print("\n" + "="*50)
            print(f"🔎 {ma_kh}")
            print(f"⏰ {res['Thoi_gian']}")
            print(f"📄 {res['Noi_dung'][:200]}...")
            print("="*50)

            # 🚫 Không ghi nếu không có lịch
            if "Không có lịch" not in res["Noi_dung"]:
                buffer.append(res)
            else:
                skip_count += 1
                print(f"⚠️ Bỏ qua ghi file: {ma_kh}")

            with lock:
                processed += 1
                percent = (processed / total) * 100
                print(f"\r📊 {processed}/{total} ({percent:.1f}%)", end="", flush=True)

            if len(buffer) >= 5:
                write_csv(output, buffer)
                buffer = []

            time.sleep(random.uniform(1.5, 3))

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
    df = pd.read_csv(input_csv)
    rows = []

    for _, row in df.iterrows():
        text = str(row["Noi_dung"])
        tg_tra_cuu = row["Thoi_gian"]

        kh = re.search(r"KHÁCH HÀNG:\s*(.+)", text)
        dc = re.search(r"ĐỊA CHỈ:\s*(.+)", text)

        blocks = re.split(r"(?=MÃ.*?LỊCH)", text, flags=re.IGNORECASE)

        for b in blocks:
            ma = re.search(r"MÃ.*LỊCH:\s*(\d+)", b)
            tg = re.search(r"từ (.+?) ngày (.+?) đến (.+?) ngày (.+)", b)
            lydo = re.search(r"LÝ DO.*:\s*(.+)", b)

            if ma:
                rows.append([
                    row["Ma_KH"],
                    kh.group(1) if kh else "",
                    dc.group(1) if dc else "",
                    ma.group(1),
                    tg.group(2) if tg else "",
                    tg.group(1) if tg else "",
                    tg.group(4) if tg else "",
                    tg.group(3) if tg else "",
                    lydo.group(1) if lydo else "",
                    tg_tra_cuu
                ])

    df2 = pd.DataFrame(rows, columns=[
        "Ma_KH","Khach_hang","Dia_chi",
        "Ma_lich","Ngay_BD","Gio_BD",
        "Ngay_KT","Gio_KT","Ly_do",
        "Thoi_gian_tra_cuu"
    ])

    df2.to_excel("output.xlsx", index=False)
    print("\n📁 Xuất output.xlsx")

    return df2

# ================= GOOGLE SHEETS =================
def upload_sheet(df):
    try:
        raw = os.getenv("GCP_JSON")
        if not raw:
            return

        raw = raw.replace("\\\\n", "\\n")
        info = json.loads(raw)
        info["private_key"] = info["private_key"].replace("\\n", "\n")

        creds = Credentials.from_service_account_info(info, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])

        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID)

        try:
            ws = sheet.worksheet(TARGET_SHEET)
        except WorksheetNotFound:
            ws = sheet.add_worksheet(title=TARGET_SHEET, rows="1000", cols="20")

        data = [df.columns.tolist()] + df.astype(str).values.tolist()

        ws.clear()
        ws.update(range_name="A1", values=data)

        print("✅ Upload Google Sheets OK")

    except Exception as e:
        print("❌ Upload lỗi:", e)

# ================= MAIN =================
if __name__ == "__main__":
    file_input = "makh_list.csv"
    file_raw = "raw.csv"

    with open(file_input, encoding="utf-8") as f:
        data = [r[0] for r in csv.reader(f) if r]

    total = len(data)

    driver_path = ChromeDriverManager().install()
    write_csv(file_raw, [], mode="w", header=True)

    threads = 3
    chunks = [data[i::threads] for i in range(threads)]

    with ThreadPoolExecutor(max_workers=threads) as ex:
        futures = [ex.submit(worker, c, driver_path, file_raw) for c in chunks]
        for f in as_completed(futures):
            f.result()

    # retry mã lỗi
    if error_list:
        print(f"\n🔁 Retry {len(error_list)} mã lỗi...")
        retry_data = list(set(error_list))
        error_list.clear()
        worker(retry_data, driver_path, file_raw)

    time.sleep(2)
    df = process(file_raw)

    print(f"\n🚫 Bỏ qua {skip_count} mã không có lịch")

    upload_sheet(df)

    print("\n🏁 DONE")
