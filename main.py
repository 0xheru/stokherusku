import os
import csv
import time
import random
import datetime
import threading
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from bs4 import BeautifulSoup

# ==========================================
# KONFIGURASI BOT & FILE
# ==========================================
TELEGRAM_TOKEN = "8671011621:AAF94MFymPkicZYOHqfD2nvPcqHnzwBClN0"
CSV_FILE = "daftar_sku-v2.csv"

# Daftar Cabang Jaknot
LOCATIONS = [
    "Gudang Online", "Jakarta Barat", "Jakarta Pusat", "Jakarta Utara",
    "Jakarta Selatan", "Cikupa", "Tangerang"
]

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# State Global Bot
is_bot_active = True
start_time = datetime.datetime.now()
current_chat_id = None
waiting_for_input = {}  # Memantau input user (tambah/hapus/cek manual)

# ==========================================
# FUNGSI HELPER & FILE CSV
# ==========================================
def get_uptime():
    """Menghitung umur bot/VPS berjalan"""
    now = datetime.datetime.now()
    delta = now - start_time
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days} hari, {hours} jam, {minutes} menit"

def load_skus():
    """Membaca file CSV SKU"""
    if not os.path.exists(CSV_FILE):
        return []
    skus = []
    with open(CSV_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if row:
                skus.append(row[0].strip())
    return list(set(skus))

def save_skus(sku_list):
    """Menyimpan ulang daftar SKU ke CSV"""
    with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        for sku in sku_list:
            writer.writerow([sku])

# ==========================================
# LOGIKA SCRAPING / CEK STOK JAKNOT
# ==========================================
def check_single_sku_from_jaknot(sku):
    """
    Melakukan scraping stok SKU dari halaman pencarian & detail Jaknot secara akurat.
    Mengembalikan teks stok asli ("Tersedia", "Sisa X", "Kosong", dll.) per cabang acuan.
    """
    search_url = f"https://www.jakartanotebook.com/search?key={sku.lower().strip()}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    
    try:
        session = requests.Session()
        resp = session.get(search_url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return None
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        product_link = soup.select_one('a[href*="/p/"]')
        
        if not product_link:
            return "NOT_FOUND"
            
        product_url = product_link.get('href')
        if not product_url.startswith('http'):
            product_url = "https://www.jakartanotebook.com" + product_url

        prod_resp = session.get(product_url, headers=headers, timeout=12)
        if prod_resp.status_code != 200:
            return None
            
        prod_soup = BeautifulSoup(prod_resp.text, 'html.parser')
        
        # Inisialisasi daftar cabang resmi acuan
        acuan_locations = [
            "Gudang Online", "Jakarta Barat", "Jakarta Pusat", 
            "Jakarta Utara", "Cikupa", "Tangerang"
        ]
        
        stock_data = {loc: "Kosong" for loc in acuan_locations}
        
        # Cari kontainer/box tempat informasi stok cabang berada
        # Elemen di Jaknot biasanya dibungkus div/box stok lokasi
        cards = prod_soup.select('div[class*="stock"], div[class*="store"], .border-rounded, div[class*="item"]')
        
        for card in cards:
            text = card.get_text(separator=' ', strip=True)
            text_lower = text.lower()
            
            for loc in acuan_locations:
                # Pencocokan nama lokasi acuan
                if loc.lower() in text_lower:
                    import re
                    
                    # 1. Cek jika ada pola angka khusus "sisa X" atau angka langsung
                    if "sisa" in text_lower:
                        nums = re.findall(r'\d+', text)
                        if nums:
                            stock_data[loc] = f"Sisa {nums[0]} pcs"
                        else:
                            stock_data[loc] = "Tersedia"
                    # 2. Cek jika tertulis Tersedia / Ready
                    elif "tersedia" in text_lower or "ready" in text_lower:
                        stock_data[loc] = "Tersedia"
                    # 3. Cek jika Pre-Order / Kosong
                    elif "kosong" in text_lower or "habis" in text_lower or "pre-order" in text_lower:
                        stock_data[loc] = "Kosong"
                    # 4. Jika ada angka pcs umum
                    elif "pcs" in text_lower:
                        nums = re.findall(r'\d+', text)
                        if nums:
                            stock_data[loc] = f"{nums[0]} pcs"

        return stock_data

    except Exception as e:
        print(f"[ERROR Scrape {sku}]: {e}")
        return None
            
        prod_soup = BeautifulSoup(prod_resp.text, 'html.parser')
        
        # 3. Scrape data stok per cabang dari elemen kotak Informasi Stok
        stock_data = {}
        
        # Cari semua elemen/box stok cabang di halaman detail
        # Jaknot biasanya membungkusnya dalam grid/box lokasi
        stock_boxes = prod_soup.select('div[class*="stock"], div[class*="store"], div[class*="location"]')
        
        # Iterasi seluruh div pencarian lokasi
        for box in prod_soup.find_all(['div', 'li', 'tr']):
            text_content = box.get_text(separator=' ', strip=True)
            
            # Cek jika baris/box mengandung nama cabang Jaknot yang valid
            valid_branches = [
                "Gudang Online", "Jakarta Barat", "Jakarta Pusat", "Jakarta Utara", 
                "Jakarta Selatan", "Tangerang", "Cikupa", "Bandung", "Surabaya", "Semarang"
            ]
            
            for branch in valid_branches:
                if branch.lower() in text_content.lower() and branch not in stock_data:
                    # Tentukan jumlah stok berdasarkan teks
                    import re
                    text_lower = text_content.lower()
                    
                    if "tersedia" in text_lower or "ready" in text_lower:
                        stock_data[branch] = 10  # Flag angka untuk status 'Tersedia'
                    elif "sisa" in text_lower:
                        nums = re.findall(r'\d+', text_content)
                        stock_data[branch] = int(nums[0]) if nums else 1
                    elif "kosong" in text_lower or "habis" in text_lower or "pre-order" in text_lower:
                        stock_data[branch] = 0
                    else:
                        # Jika ada angka langsung
                        nums = re.findall(r'\d+', text_content)
                        if nums:
                            stock_data[branch] = int(nums[0])

        return stock_data if stock_data else None

    except Exception as e:
        print(f"[ERROR Scrape {sku}]: {e}")
        return None
            
        prod_soup = BeautifulSoup(prod_resp.text, 'html.parser')
        
        # 3. Scrape data stok per cabang
        stock_data = {}
        
        branches = prod_soup.select('.store-location-item, .location-stock, tr.store-row, .branch-item')
        
        if not branches:
            branches = prod_soup.find_all(['tr', 'div'], class_=lambda c: c and ('store' in c or 'location' in c or 'branch' in c))

        for b in branches:
            text_full = b.text.strip()
            if not text_full:
                continue
                
            name_elem = b.select_one('.store-name, .location-name, .branch-name, td:first-child')
            qty_elem = b.select_one('.store-stock, .stock-qty, .stock-status, td:last-child')
            
            if name_elem and qty_elem:
                b_name = name_elem.text.strip()
                b_qty_text = qty_elem.text.strip()
                
                import re
                nums = re.findall(r'\d+', b_qty_text)
                if nums:
                    qty = int(nums[0])
                elif any(word in b_qty_text.lower() for word in ['ready', 'ada', 'tersedia']):
                    qty = 10
                else:
                    qty = 0
                    
                if b_name:
                    stock_data[b_name] = qty

        if not stock_data:
            stock_data = {"Gudang Online": 1}

        return stock_data

    except Exception as e:
        print(f"[ERROR Scrape {sku}]: {e}")
        return None
def process_all_skus(chat_id=None):
    """Memproses seluruh SKU dengan delay aman 5-8 detik"""
    target_chat = chat_id if chat_id else current_chat_id
    if not target_chat:
        print("[WARNING] Chat ID belum terdaftar. Kirim /start di Telegram terlebih dahulu.")
        return

    skus = load_skus()
    if not skus:
        bot.send_message(target_chat, "⚠️ Daftar SKU kosong di `daftar_sku-v2.csv`.")
        return

    bot.send_message(target_chat, f"⏳ *Memulai pengecekan {len(skus)} SKU...*\n_Estimasi delay 5–8 detik per SKU untuk keamanan IP._", parse_mode="Markdown")

    ready_list = []
    empty_list = []
    not_found_list = []
    warning_list = []

    for idx, sku in enumerate(skus, start=1):
        # Stop jika bot dimatikan di tengah jalan
        if not is_bot_active and not chat_id:
            bot.send_message(target_chat, "🛑 Pengecekan otomatis dihentikan karena status bot NONAKTIF.")
            return

        res = check_single_sku_from_jaknot(sku)
        
        if res == "NOT_FOUND" or res is None:
            not_found_list.append(sku)
        else:
            # Analisis cabang & stok
            active_branches = {}
            total_stock_all = 0
            
            for branch, qty in res.items():
                if qty > 0:
                    active_branches[branch] = qty
                    total_stock_all += qty

            if not active_branches:
                empty_list.append(sku)
            else:
                # Format detail cabang ready
                branch_str = ", ".join([f"{b}: {q}pcs" for b, q in active_branches.items()])
                ready_list.append(f"• `{sku}` ➔ {branch_str}")

                # Logika Warning: Stok <= 10 DAN HANYA di 1 cabang/gudang
                if len(active_branches) == 1:
                    branch_name, qty = list(active_branches.items())[0]
                    if qty <= 10:
                        warning_list.append(f"⚠️ `{sku}` sisa *{qty} pcs* HANYA di *{branch_name}*")

        # Delay acak 5-8 detik per SKU
        if idx < len(skus):
            time.sleep(random.uniform(5.0, 8.0))

    # Construct Laporan
    msg = f"📊 *LAPORAN STOK JAKNOT*\n_{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n\n"
    
    if ready_list:
        msg += "✅ *SKU READY:*\n" + "\n".join(ready_list) + "\n\n"
    if empty_list:
        msg += "❌ *SKU KOSONG:*\n" + ", ".join([f"`{s}`" for s in empty_list]) + "\n\n"
    if not_found_list:
        msg += "❓ *SKU TIDAK MATCH / DELETED:*\n" + ", ".join([f"`{s}`" for s in not_found_list]) + "\n\n"
    if warning_list:
        msg += "🚨 *STOK MENIPIS (WARNING):*\n" + "\n".join(warning_list) + "\n\n"

    bot.send_message(target_chat, msg, parse_mode="Markdown")

# ==========================================
# MENU INLINE KEYBOARD
# ==========================================
def main_menu_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    
    btn_off = InlineKeyboardButton("🔴 Matikan Bot", callback_data="btn_stop")
    btn_on = InlineKeyboardButton("🟢 Hidupkan Bot", callback_data="btn_start")
    btn_cek_realtime = InlineKeyboardButton("⚡ Cek Stok Sekarang", callback_data="btn_cek_now")
    btn_cek_manual = InlineKeyboardButton("🔍 Cek SKU Manual", callback_data="btn_cek_manual")
    btn_add_sku = InlineKeyboardButton("➕ Tambah SKU", callback_data="btn_add_sku")
    btn_del_sku = InlineKeyboardButton("🗑️ Hapus SKU", callback_data="btn_del_sku")
    
    markup.add(btn_off, btn_on)
    markup.add(btn_cek_realtime)
    markup.add(btn_add_sku, btn_del_sku)
    markup.add(btn_cek_manual)
    return markup

# ==========================================
# COMMAND & CALLBACK TELEGRAM
# ==========================================
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    global current_chat_id
    current_chat_id = message.chat.id
    
    status_str = "🟢 *AKTIF*" if is_bot_active else "🔴 *NONAKTIF*"
    uptime_str = get_uptime()
    
    text = (
        f"🤖 *CONTROL PANEL BOT STOK JAKNOT*\n\n"
        f"• *Status Bot:* {status_str}\n"
        f"• *Umur VPS/Bot:* `{uptime_str}`\n\n"
        f"Silakan pilih menu di bawah ini:"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def handle_inline_buttons(call):
    global is_bot_active
    chat_id = call.message.chat.id

    if call.data == "btn_stop":
        is_bot_active = False
        bot.answer_callback_query(call.id, "Bot dimatikan!")
        bot.send_message(chat_id, "🔴 *Bot Otomatis Diberhentikan.*", parse_mode="Markdown")
        
    elif call.data == "btn_start":
        is_bot_active = True
        bot.answer_callback_query(call.id, "Bot diaktifkan!")
        bot.send_message(chat_id, "🟢 *Bot Otomatis Diaktifkan Kembali.*", parse_mode="Markdown")

    elif call.data == "btn_cek_now":
        bot.answer_callback_query(call.id, "Memulai cek stok realtime...")
        threading.Thread(target=process_all_skus, args=(chat_id,)).start()

    elif call.data == "btn_add_sku":
        waiting_for_input[chat_id] = "ADD_SKU"
        bot.send_message(chat_id, "✍️ Kirimkan kode *SKU baru* yang ingin ditambahkan:")

    elif call.data == "btn_del_sku":
        waiting_for_input[chat_id] = "DEL_SKU"
        bot.send_message(chat_id, "✍️ Kirimkan kode *SKU* yang ingin dihapus:")

    elif call.data == "btn_cek_manual":
        waiting_for_input[chat_id] = "CEK_MANUAL"
        bot.send_message(chat_id, "🔍 Kirimkan kode *1 SKU* yang ingin Anda cek secara manual:")

# Handler Penerima Text Input (Tambah/Hapus/Cek Manual)
@bot.message_handler(func=lambda message: message.chat.id in waiting_for_input)
def handle_user_input(message):
    chat_id = message.chat.id
    action = waiting_for_input.get(chat_id)
    text = message.text.strip().upper()
    skus = load_skus()

    if action == "ADD_SKU":
        if text in skus:
            bot.send_message(chat_id, f"⚠️ SKU `{text}` sudah ada di dalam daftar.", parse_mode="Markdown")
        else:
            skus.append(text)
            save_skus(skus)
            bot.send_message(chat_id, f"✅ Berhasil menambah SKU `{text}` ke `daftar_sku-v2.csv`.", parse_mode="Markdown")

    elif action == "DEL_SKU":
        if text in skus:
            skus.remove(text)
            save_skus(skus)
            bot.send_message(chat_id, f"🗑️ Berhasil menghapus SKU `{text}` dari daftar.", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, f"⚠️ SKU `{text}` tidak ditemukan di dalam daftar.", parse_mode="Markdown")

    elif action == "CEK_MANUAL":
        bot.send_message(chat_id, f"🔍 Mengecek SKU `{text}`...", parse_mode="Markdown")
        res = check_single_sku_from_jaknot(text)
        
        if res == "NOT_FOUND" or res is None:
            bot.send_message(chat_id, f"❌ SKU `{text}` tidak ditemukan/tidak match di Jaknot.", parse_mode="Markdown")
        else:
            branches_info = []
            for branch in LOCATIONS:
                qty = res.get(branch, 0)
                status = f"✅ {qty} pcs" if qty > 0 else "❌ Kosong"
                branches_info.append(f"• *{branch}:* {status}")
            
            out = f"📌 *DETAIL STOK MANUAL SKU:* `{text}`\n\n" + "\n".join(branches_info)
            bot.send_message(chat_id, out, parse_mode="Markdown")

    del waiting_for_input[chat_id]

# ==========================================
# JADWAL OTOMATIS BERKALA (EVERY 1 HOUR)
# ==========================================
def schedule_loop():
    while True:
        time.sleep(3600)  # Cek setiap 1 jam (3600 detik)
        if is_bot_active:
            print("[INFO] Menjalankan pengecekan otomatis berkala...")
            process_all_skus()

# ==========================================
# MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    print("[INFO] Bot Stok Jaknot Interaktif Siap...")
    
    # Run Background Schedule
    t = threading.Thread(target=schedule_loop, daemon=True)
    t.start()
    
    # Run Telegram Listener
    bot.infinity_polling()
