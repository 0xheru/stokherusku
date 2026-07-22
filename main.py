import json
import re
import time
import requests
import pandas as pd
import schedule

# ==========================================
# KONFIGURASI BOT TELEGRAM & AKSES
# ==========================================
TELEGRAM_BOT_TOKEN = "MASUKKAN_TOKEN_BOT_TELEGRAM_ANDA"
TELEGRAM_CHAT_ID = "MASUKKAN_CHAT_ID_TELEGRAM_ANDA"
FILE_SKU = "daftar_sku-v2.csv"  # Menggunakan file CSV 139 SKU

# 6 Branch ID Acuan Anda (Gudang Online, JakPus, JakBar, JakUt, Tangerang, Cikupa)
TARGET_BRANCHES = {
    "0yjwK5": "Gudang Online",
    "jz23mo": "Jakarta Pusat",
    "rz5DK2": "Jakarta Barat",
    "GmLpmq": "Jakarta Utara",
    "gKBkzB": "Tangerang",
    "ezZ7xm": "Cikupa"
}

def send_telegram_msg(message):
    """Fungsi untuk mengirim pesan ke Telegram"""
    url = "https://api.telegram.org/bot{8671011621:AAGuzfVMO0itX3Qr7IVcB4VKnAe5RVxQFNg}/sendMessage"
    payload = {
        "chat_id": 1342364928,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Gagal mengirim pesan Telegram: {e}")

def check_all_sku_job():
    print("\n[INFO] Memulai pengecekan stok berkala...")
    
    # 1. Baca File Excel / CSV
    try:
        if FILE_SKU.endswith('.csv'):
            df = pd.read_csv(FILE_SKU)
        else:
            df = pd.read_excel(FILE_SKU)
    except Exception as e:
        print(f"[ERROR] Gagal membaca file {FILE_SKU}: {e}")
        return

    empty_skus = []      # Menyimpan daftar SKU yang KOSONG DI SEMUA LOKASI
    available_count = 0  # Menghitung SKU yang masih aman
    total_sku = len(df)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # 2. Iterasi Cek Setiap SKU dari File
    for index, row in df.iterrows():
        sku = str(row['sku']).strip()
        url = str(row['url']).strip()
        
        try:
            res = requests.get(url, headers=headers, timeout=10)
            
            # Ekstrak data Apollo State JSON dari HTML
            match = re.search(r'__NEXT_DATA__"\s*type="application/json">(.*?)</script>', res.text)
            
            if match:
                json_data = json.loads(match.group(1))
                apollo_state = json_data.get("props", {}).get("pageProps", {}).get("apolloState", {})
                
                # Cari data variant berdasarkan SKU
                is_any_location_available = False
                
                for key, val in apollo_state.items():
                    if key.startswith("ProductVariantSku") and val.get("id") == sku:
                        stocks = val.get("stocks", [])
                        
                        # Cek hanya di 6 cabang acuan
                        for st in stocks:
                            branch_id = st.get("branchId")
                            is_available = st.get("isStockAvailable", False)
                            
                            if branch_id in TARGET_BRANCHES and is_available:
                                is_any_location_available = True
                                break
                        break

                if is_any_location_available:
                    available_count += 1
                else:
                    empty_skus.append(sku)
            else:
                print(f"[WARNING] Tidak dapat mengambil data JSON dari SKU: {sku}")
                
        except Exception as e:
            print(f"[ERROR] Gagal mengecek SKU {sku}: {e}")
            
        time.sleep(1) # Jeda 1 detik antar request agar aman dari blokir

    # 3. Kirim Laporan ke Telegram
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    
    if len(empty_skus) == 0:
        # Jika SEMUA SKU aman / tersedia di minimal 1 lokasi acuan
        msg = (
            f"✅ *LAPORAN STOK SUPPLIER*\n"
            f"⏰ _Waktu: {now_str}_\n\n"
            f"Semua SKU pada daftar stok ({available_count}/{total_sku} item) *MASIH TERSEDIA* di Jabodetabek."
        )
        send_telegram_msg(msg)
    else:
        # Jika ADA SKU yang KOSONG DI SEMUA LOKASI ACUAN
        empty_list_str = "\n".join([f"• `{s}`" for s in empty_skus])
        msg = (
            f"⚠️ *PERINGATAN STOK KOSONG*\n"
            f"⏰ _Waktu: {now_str}_\n\n"
            f"Ditemukan *{len(empty_skus)} WADUH WK SKU KOSONG* di semua lokasi acuan (Gudang Online & Jabodetabek):\n\n"
            f"{empty_list_str}\n\n"
            f"💡 _Segera Kosongkan SKU produk tersebut di toko online Anda!_"
        )
        send_telegram_msg(msg)

# ==========================================
# PENJADWALAN AUTOMATION (SETIAP 1 JAM)
# ==========================================
# Jalankan sekali saat script baru dinyalakan
check_all_sku_job()

# Jadwalkan eksekusi otomatis setiap 1 jam
schedule.every(1).hours.do(check_all_sku_job)

print("[SYSTEM] Bot monitoring stok berjalan... Menunggu jadwal berikutnya Ntar w info lagi cuk.")
while True:
    schedule.run_pending()
    time.sleep(10)
