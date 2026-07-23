import os
import re
import csv
import json
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
TELEGRAM_TOKEN = "8671011621:AAGOyNiVNP90fkY-D_4DHitQKGTWBdRxKz0"
CSV_FILE = "daftar_sku-v2.csv"
USERS_FILE = "registered_users.txt"
STATE_FILE = "bot_state.json"  # File database baru untuk memori bot

LOCATIONS = [
    "Gudang Online", "Jakarta Barat", "Jakarta Pusat", 
    "Jakarta Utara", "Cikupa", "Tangerang"
]

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}
is_auto_active = True

# ==========================================
# FUNGSI KELOLA DATA & MEMORI BOT
# ==========================================
def save_chat_id(chat_id):
    users = get_registered_users()
    if str(chat_id) not in users:
        with open(USERS_FILE, "a") as f:
            f.write(f"{chat_id}\n")

def get_registered_users():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]

def load_skus():
    if not os.path.exists(CSV_FILE):
        return []
    skus = []
    with open(CSV_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if row:
                skus.append(row[0].strip().upper())
    return list(dict.fromkeys(skus))

def save_skus(sku_list):
    with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        for sku in sku_list:
            writer.writerow([sku])

def load_state():
    """Membaca memori histori bot (harga & stok)"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"oos_since": {}, "last_price": {}, "price_alerts": {}}

def save_state(state):
    """Menyimpan memori histori bot (harga & stok)"""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def format_rupiah(angka):
    return f"Rp.{angka:,}".replace(',', '.')

# ==========================================
# LOGIKA SCRAPING / CEK STOK & HARGA
# ==========================================
def check_single_sku_from_jaknot(sku):
    search_url = f"https://www.jakartanotebook.com/search?key={sku.lower().strip()}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    
    try:
        session = requests.Session()
        resp = session.get(search_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        product_link = soup.select_one('a[href*="/p/"]')
        if not product_link:
            return "NOT_FOUND"

        stock_data = {loc: "Kosong" for loc in LOCATIONS}
        product_card = product_link.find_parent('div', class_=lambda c: c and ('product' in c or 'item' in c or 'card' in c))
        if not product_card:
            product_card = soup

        lines = [line.strip() for line in product_card.get_text(separator='\n').split('\n') if line.strip()]
        price_int = 0

        for i, line in enumerate(lines):
            # Cek Harga
            if price_int == 0:
                price_match = re.search(r'Rp\.?\s*([\d\.]+)', line)
                if price_match:
                    try:
                        price_int = int(price_match.group(1).replace('.', ''))
                    except:
                        pass
            
            # Cek Stok
            for loc in LOCATIONS:
                if loc.lower() in line.lower():
                    context_text = " ".join(lines[i:i+3]).lower()
                    if "sisa" in context_text:
                        nums = re.findall(r'\d+', context_text)
                        stock_data[loc] = f"Sisa {nums[0]} pcs" if nums else "Tersedia"
                    elif "tersedia" in context_text or "ready" in context_text:
                        stock_data[loc] = "Tersedia"
                    elif "on restock" in context_text:
                        stock_data[loc] = "On Restock"
                    elif "habis" in context_text or "kosong" in context_text or "pre order" in context_text or "pre-order" in context_text:
                        stock_data[loc] = "Kosong"

        return {"stocks": stock_data, "price": price_int}
    except Exception as e:
        print(f"[ERROR Scrape {sku}]: {e}")
        return None

# ==========================================
# LOGIKA FILTER LAPORAN
# ==========================================
def should_report_sku(stocks):
    """Filter khusus stok kritis dan kosong total."""
    gudang_online = stocks.get("Gudang Online", "Kosong")
    if gudang_online == "Tersedia" or "Sisa" in gudang_online:
        return False
        
    branch_statuses = [v for k, v in stocks.items() if k != "Gudang Online"]
    if "Tersedia" in branch_statuses:
        return False

    all_empty = all(s in ["Kosong", "On Restock"] for s in branch_statuses)
    if all_empty:
        return True
        
    has_low_stock = False
    for s in branch_statuses:
        if "Sisa" in s:
            nums = re.findall(r'\d+', s)
            if nums and int(nums[0]) < 11:
                has_low_stock = True
                
    if has_low_stock:
        return True

    return False

# ==========================================
# PROSES ALL SKU & TRACKING CERDAS
# ==========================================
def process_all_skus(chat_id=None, is_auto=False):
    try:
        skus = load_skus()
        if not skus:
            if chat_id:
                bot.send_message(chat_id, "⚠️ File CSV SKU kosong.")
            return

        if chat_id and not is_auto:
            bot.send_message(chat_id, f"⌛ Memulai pengecekan {len(skus)} SKU...\nMelakukan update Harga & Stok Kritis.")

        state = load_state()
        filtered_report = {}
        not_matched = []
        
        current_date_str = datetime.datetime.now().strftime("%d/%m/%Y")
        current_dt = datetime.datetime.now()

        for sku in skus:
            try:
                res = check_single_sku_from_jaknot(sku)
                if res == "NOT_FOUND" or res is None:
                    not_matched.append(sku)
                else:
                    stocks = res["stocks"]
                    price = res["price"]
                    
                    is_critical = should_report_sku(stocks)
                    is_ready_again = False
                    oos_date = None
                    alert_text = ""

                    # 1. TRACKING STOK KOSONG -> READY KEMBALI
                    if is_critical:
                        if sku not in state["oos_since"]:
                            state["oos_since"][sku] = current_date_str
                    else:
                        if sku in state["oos_since"]:
                            is_ready_again = True
                            oos_date = state["oos_since"][sku]
                            del state["oos_since"][sku]

                    # 2. TRACKING KENAIKAN HARGA
                    old_price = state["last_price"].get(sku, 0)
                    if old_price > 0 and price > old_price:
                        # Harga Naik!
                        state["price_alerts"][sku] = {
                            "old": old_price,
                            "new": price,
                            "date": current_dt.strftime("%Y-%m-%d")
                        }
                    
                    if price > 0:
                        state["last_price"][sku] = price

                    # Cek Notifikasi Harga (Berlaku 3 Hari)
                    has_price_alert = False
                    if sku in state["price_alerts"]:
                        alert_info = state["price_alerts"][sku]
                        alert_dt = datetime.datetime.strptime(alert_info["date"], "%Y-%m-%d")
                        if (current_dt - alert_dt).days > 3:
                            del state["price_alerts"][sku]
                        else:
                            has_price_alert = True
                            alert_text = f"Pringatan Harga Naik dari {format_rupiah(alert_info['old'])} saat ini menjadi {format_rupiah(alert_info['new'])}"

                    # Masukkan ke laporan jika Kritis / Ready Kembali / Harga Naik
                    if is_critical or is_ready_again or has_price_alert:
                        filtered_report[sku] = {
                            "stocks": stocks,
                            "is_ready_again": is_ready_again,
                            "oos_date": oos_date,
                            "alert_text": alert_text
                        }

            except Exception:
                not_matched.append(sku)
            time.sleep(random.randint(3, 5))

        save_state(state) # Simpan database bot

        # ========================================
        # FORMATTING PESAN TELEGRAM
        # ========================================
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if not filtered_report and not not_matched:
            msg = f"📊 **LAPORAN STOK JAKNOT**\n{now}\n\n✅ **Semua SKU Aman!** Tidak ada SKU kritis, stok ready kembali, atau peringatan harga naik."
        else:
            msg = f"📊 **LAPORAN UPDATE JAKNOT**\n{now}\n\n"
            for sku, data in filtered_report.items():
                msg += f"🔹 **SKU: {sku}**\n"
                
                # Tanda Ready Kembali
                if data["is_ready_again"]:
                    msg += f"    ✅ Kosong sejak tanggal {data['oos_date']}, Saat ini sudah ready kembali.\n"
                
                # Tanda Harga Naik
                if data["alert_text"]:
                    msg += f"    ⚠️ {data['alert_text']}\n"

                # Status Cabang
                for loc, status in data["stocks"].items():
                    icon = "✅" if status in ["Tersedia"] or "Sisa" in status else "❌"
                    msg += f"  • {loc}: {icon} {status}\n"
                msg += "\n"

        if not_matched:
            msg += f"❓ **SKU TIDAK MATCH / ERROR ({len(not_matched)} SKU):**\n" + ", ".join(not_matched[:20])

        targets = [chat_id] if chat_id and not is_auto else get_registered_users()
        
        for cid in targets:
            try:
                if len(msg) > 4000:
                    for x in range(0, len(msg), 4000):
                        bot.send_message(cid, msg[x:x+4000], parse_mode="Markdown")
                else:
                    bot.send_message(cid, msg, parse_mode="Markdown")
            except Exception as e:
                print(f"[ERROR Kirim ke {cid}]: {e}")
    except Exception as general_err:
        print(f"[CRITICAL ERROR]: {general_err}")

def schedule_checker():
    """Thread penjadwalan otomatis setiap 2 jam (7200 detik)"""
    while True:
        if is_auto_active:
            print("[INFO] Jalankan Pengecekan Otomatis (Per 2 Jam)...")
            process_all_skus(is_auto=True)
        time.sleep(7200)

# ==========================================
# TELEGRAM MENU & KEYBOARD
# ==========================================
def get_main_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("⚡ Cek Stok Sekarang", callback_data="check_now"))
    markup.row(
        InlineKeyboardButton("➕ Tambah SKU", callback_data="add_sku"),
        InlineKeyboardButton("🗑️ Hapus SKU", callback_data="del_sku")
    )
    markup.row(InlineKeyboardButton("🔍 Cek SKU Manual", callback_data="manual_sku"))
    
    toggle_text = "🔴 Matikan Auto-Check" if is_auto_active else "🟢 Hidupkan Auto-Check"
    markup.row(
        InlineKeyboardButton(toggle_text, callback_data="toggle_auto"),
        InlineKeyboardButton("📌 Status Bot", callback_data="bot_status")
    )
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    save_chat_id(message.chat.id)
    bot.send_message(
        message.chat.id,
        "👋 Selamat datang di Bot Pantau Stok Jaknot!\nID Chat Anda berhasil terdaftar untuk menerima laporan otomatis per 2 jam.\n\nPilih menu di bawah ini:",
        reply_markup=get_main_keyboard()
    )

@bot.callback_query_handler(func=lambda call: True)
def callback_listener(call):
    global is_auto_active
    chat_id = call.message.chat.id
    save_chat_id(chat_id)
    
    if call.data == "check_now":
        bot.answer_callback_query(call.id, "Memulai pengecekan...")
        threading.Thread(target=process_all_skus, args=(chat_id, False)).start()
        
    elif call.data == "toggle_auto":
        is_auto_active = not is_auto_active
        status_txt = "diaktifkan 🟢" if is_auto_active else "dimatikan 🔴"
        bot.answer_callback_query(call.id, f"Auto-Check 2 Jam {status_txt}")
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=get_main_keyboard())
        
    elif call.data == "bot_status":
        status_str = "🟢 AKTIF (Cek tiap 2 jam)" if is_auto_active else "🔴 NONAKTIF (Auto-check mati)"
        skus = load_skus()
        users = get_registered_users()
        msg_status = (
            f"📌 **STATUS BOT STOK JAKNOT**\n\n"
            f"• **Auto-Check 2 Jam:** {status_str}\n"
            f"• **Total SKU Dipantau:** {len(skus)} SKU\n"
            f"• **Penerima Laporan:** {len(users)} Chat ID\n"
            f"• **Fitur Tambahan:** Deteksi Harga Naik (3 Hari) & Info Ready Kembali\n"
            f"• **Lokasi Cabang:** 6 Cabang Utama"
        )
        bot.send_message(chat_id, msg_status, parse_mode="Markdown", reply_markup=get_main_keyboard())
        
    elif call.data == "add_sku":
        user_states[chat_id] = "WAITING_ADD"
        bot.send_message(chat_id, "Kirimkan kode SKU yang ingin ditambahkan:")
    elif call.data == "del_sku":
        user_states[chat_id] = "WAITING_DEL"
        bot.send_message(chat_id, "Kirimkan kode SKU yang ingin dihapus:")
    elif call.data == "manual_sku":
        user_states[chat_id] = "WAITING_MANUAL"
        bot.send_message(chat_id, "🔍 Kirimkan kode *1 SKU* yang ingin Anda cek secara manual:", parse_mode="Markdown")

@bot.message_handler(func=lambda msg: True)
def text_listener(message):
    chat_id = message.chat.id
    save_chat_id(chat_id)
    state = user_states.get(chat_id)
    text = message.text.strip().upper()

    if state == "WAITING_ADD":
        skus = load_skus()
        if text not in skus:
            skus.append(text)
            save_skus(skus)
            bot.reply_to(message, f"✅ SKU *{text}* berhasil ditambahkan!", parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            bot.reply_to(message, f"⚠️ SKU *{text}* sudah ada di daftar.", reply_markup=get_main_keyboard())
        user_states[chat_id] = None

    elif state == "WAITING_DEL":
        skus = load_skus()
        if text in skus:
            skus.remove(text)
            save_skus(skus)
            bot.reply_to(message, f"🗑️ SKU *{text}* berhasil dihapus!", parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            bot.reply_to(message, f"⚠️ SKU *{text}* tidak ditemukan di daftar.", reply_markup=get_main_keyboard())
        user_states[chat_id] = None

    elif state == "WAITING_MANUAL":
        bot.send_message(chat_id, f"🔍 Mengecekan SKU *{text}*...", parse_mode="Markdown")
        res = check_single_sku_from_jaknot(text)
        
        if res and res != "NOT_FOUND":
            stocks = res["stocks"]
            price = res["price"]
            msg = f"📌 *DETAIL STOK MANUAL SKU: {text}*\n"
            msg += f"💰 *Harga Saat Ini:* {format_rupiah(price) if price > 0 else 'Tidak ditemukan'}\n\n"
            
            for loc, status in stocks.items():
                icon = "✅" if status in ["Tersedia"] or "Sisa" in status else "❌"
                msg += f"• *{loc}:* {icon} {status}\n"
            bot.reply_to(message, msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
        elif res == "NOT_FOUND":
            bot.reply_to(message, f"❌ SKU *{text}* tidak ditemukan/tidak match di Jaknot.", parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            bot.reply_to(message, f"⚠️ Gagal mengambil data untuk SKU *{text}*.", parse_mode="Markdown", reply_markup=get_main_keyboard())
        
        user_states[chat_id] = None

if __name__ == "__main__":
    print("[INFO] Bot Stok Jaknot Interaktif Siap...")
    threading.Thread(target=schedule_checker, daemon=True).start()
    bot.infinity_polling()
