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
    options.add_argument("--headless=new") # Đổi thành # nếu muốn hiện trình duyệt để debug
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("window-size=1920,1080")
    
    # Giả lập User-Agent để tránh bị hệ thống nhận diện bot
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    options.page_load_strategy = "normal"

    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver

# ================= SCRAPE =================
def scrape_fast(driver, ma_kh, max_retry=3):
    thoi_gian = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for attempt in range(max_retry):
        try:
            # 1. Chờ ô nhập liệu sẵn sàng
            input_el = WebDriverWait(driver, 15).until(
                lambda d: d.find_element(By.ID, "idMaKhachHang")
            )

            # 2. Xóa sạch dữ liệu cũ bằng JS để đảm bảo khung kết quả trống rỗng
            driver.execute_script("arguments[0].value = '';", input_el)
            driver.execute_script("document.getElementById('idThongTinLichNgungGiamMaKhachHang').innerHTML = 'LOADING_NEW';")
            
            time.sleep(0.5)
            input_el.send_keys(ma_kh)
            input_el.send_keys(Keys.RETURN)

            # 3. Đợi cho đến khi chữ 'LOADING_NEW' biến mất và xuất hiện kết quả thực tế
            # Điều kiện: Nội dung mới phải chứa một trong các từ khóa hợp lệ
            def wait_for_data(d):
                text = d.find_element(By.ID, "idThongTinLichNgungGiamMaKhachHang").text.strip()
                if "LOADING_NEW" in text or text == "":
                    return False
                # Chấp nhận nếu có lịch hoặc xác nhận không có lịch
                return any(kw in text for kw in ["MÃ", "LỊCH", "Không có lịch", "KHÁCH HÀNG"])

            WebDriverWait(driver, 20).until(wait_for_data)
            
            # Nghỉ một nhịp ngắn để JS render nốt dữ liệu
            time.sleep(1)
            content = driver.find_element(By.ID, "idThongTinLichNgungGiamMaKhachHang").text.strip()

            # Trả về kết quả
            return {
                "Ma_KH": ma_kh,
                "Thoi_gian": thoi_gian,
                "Noi_dung": content
            }

        except Exception as e:
            print(f"\n🔁 Retry {attempt+1} | {ma_kh} | Lỗi: {str(e)[:50]}")
            driver.refresh()
            time.sleep(3)

    with lock:
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

        for ma_kh in data:
            res = scrape_fast(driver, ma_kh)

            # Chỉ xử lý nếu lấy được dữ liệu thực
            if "Lỗi -" not in res["Noi_dung"]:
                if "Không có lịch" not in res["Noi_dung"]:
                    buffer.append(res)
                else:
                    with lock:
                        skip_count += 1
            
            with lock:
                processed += 1
                percent = (processed / total) * 100
                print(f"\r📊 Tiến độ: {processed}/{total} ({percent:.1f}%) | Bỏ qua: {skip_count}", end="", flush=True)

            if len(buffer) >= 5:
                write_csv(output, buffer)
                buffer = []

            # Giãn cách nhẹ để tránh bị hệ thống chặn
            time.sleep(random.uniform(1, 2))

        if buffer:
            write_csv(output, buffer)

    finally:
        driver.quit()

# ================= CSV =================
def write_csv(file, rows, mode='a', header=False):
    if not rows and not header: return
    with csv_lock:
        with open(file, mode, newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=["Ma_KH", "Thoi_gian", "Noi_dung"])
            if header:
                writer.writeheader()
            writer.writerows(rows)

# ================= XỬ LÝ REGEX =================
def process(input_csv):
    if not os.path.exists(input_csv) or os.stat(input_csv).st_size == 0:
        print("⚠️ File raw.csv trống hoặc không tồn tại.")
        return pd.DataFrame()

    df = pd.read_csv(input_csv)
    rows = []

    for _, row in df.iterrows():
        text = str(row["Noi_dung"])
        tg_tra_cuu = row["Thoi_gian"]

        # Tìm thông tin chung
        kh = re.search(r"KHÁCH HÀNG:\s*(.+)", text)
        dc = re.search(r"ĐỊA CHỈ:\s*(.+)", text)

        # Tách từng khối lịch (Trường hợp 1 mã KH có nhiều lịch cúp điện)
        blocks = re.split(r"(?=MÃ.*?LỊCH)", text, flags=re.IGNORECASE)

        for b in blocks:
            if "MÃ" not in b.upper(): continue
            
            ma = re.search(r"MÃ.*?LỊCH:\s*(\d+)", b, re.I)
            tg = re.search(r"từ (.+?) ngày (.+?) đến (.+?) ngày (.+)", b, re.I)
            lydo = re.search(r"LÝ DO.*:\s*(.+)", b, re.I)

            if ma:
                rows.append([
                    row["Ma_KH"],
                    kh.group(1).strip() if kh else "",
                    dc.group(1).strip() if dc else "",
                    ma.group(1).strip(),
                    tg.group(2).strip() if tg else "", # Ngay_BD
                    tg.group(1).strip() if tg else "", # Gio_BD
                    tg.group(4).strip() if tg else "", # Ngay_KT
                    tg.group(3).strip() if tg else "", # Gio_KT
                    lydo.group(1).strip() if lydo else "",
                    tg_tra_cuu
                ])

    df2 = pd.DataFrame(rows, columns=[
        "Ma_KH","Khach_hang","Dia_chi",
        "Ma_lich","Ngay_BD","Gio_BD",
        "Ngay_KT","Gio_KT","Ly_do",
        "Thoi_gian_tra_cuu"
    ])

    df2.to_excel("output.xlsx", index=False)
    print(f"\n📁 Đã xuất {len(df2)} dòng vào output.xlsx")
    return df2

# ================= GOOGLE SHEETS =================
def upload_sheet(df):
    if df.empty:
        print("⚠️ Không có dữ liệu để upload.")
        return
    try:
        raw = os.getenv("GCP_JSON")
        if not raw:
            print("⚠️ Thiếu biến môi trường GCP_JSON, không thể upload.")
            return

        info = json.loads(raw.replace("\\\\n", "\\n"))
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
        ws.update(values=data, range_name="A1")
        print("✅ Đã cập nhật Google Sheets thành công!")

    except Exception as e:
        print("❌ Lỗi Google Sheets:", e)

# ================= MAIN =================
if __name__ == "__main__":
    file_input = "makh_list.csv"
    file_raw = "raw.csv"

    if not os.path.exists(file_input):
        print(f"❌ Không tìm thấy file {file_input}")
        exit()

    with open(file_input, encoding="utf-8") as f:
        data = [r[0] for r in csv.reader(f) if r]

    total = len(data)
    print(f"🚀 Bắt đầu tra cứu {total} mã khách hàng...")

    driver_path = ChromeDriverManager().install()
    write_csv(file_raw, [], mode="w", header=True)

    threads = 3 # Có thể tăng lên 5 nếu máy mạnh
    chunks = [data[i::threads] for i in range(threads)]

    with ThreadPoolExecutor(max_workers=threads) as ex:
        futures = [ex.submit(worker, c, driver_path, file_raw) for c in chunks]
        for f in as_completed(futures):
            f.result()

    # Retry cho những mã bị lỗi kỹ thuật
    if error_list:
        print(f"\n🔁 Đang tra cứu lại {len(error_list)} mã bị lỗi...")
        retry_data = list(set(error_list))
        error_list = []
        worker(retry_data, driver_path, file_raw)

    print("\n⌛ Đang xử lý dữ liệu và bóc tách...")
    final_df = process(file_raw)
    
    print(f"🚫 Tổng số mã không có lịch: {skip_count}")
    
    if not final_df.empty:
        upload_sheet(final_df)

    print("\n🏁 HOÀN THÀNH!")
