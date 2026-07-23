import os
import re
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
TELEGRAM_TOKEN = "8671011621:AAGOyNiVNP90fkY-D_4DHitQKGTWBdRxKz0"
CSV_FILE = "daftar_sku-v2.csv"
USERS_FILE = "registered_users.txt"

LOCATIONS = [
    "Gudang Online", "Jakarta Barat", "Jakarta Pusat", 
    "Jakarta Utara", "Cikupa", "Tangerang"
]

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}
is_auto_active = True

# ==========================================
# FUNGSI KELOLA USER & CSV
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

# ==========================================
# LOGIKA SCRAPING / CEK STOK JAKNOT
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

        for i, line in enumerate(lines):
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

        return stock_data
    except Exception as e:
        print(f"[ERROR Scrape {sku}]: {e}")
        return None

# ==========================================
# LOGIKA FILTER LAPORAN PERMINTAAN OM
# ==========================================
def should_report_sku(stocks):
    """
    Syarat Lapor:
    1. Gudang Online Tersedia -> SKIP (TIDAK LAPOR)
    2. Semua Cabang Kosong -> LAPOR
    3. Gudang Online Kosong, tapi ada cabang yang stoknya di bawah 11 pcs (Sisa X pcs) -> LAPOR
    4. Ada cabang yang statusnya 'Tersedia' (stok banyak) -> SKIP
    """
    gudang_online = stocks.get("Gudang Online", "Kosong")
    
    # Jika Gudang Online Ready -> Jangan dilaporkan
    if gudang_online == "Tersedia" or "Sisa" in gudang_online:
        return False
        
    branch_statuses = [v for k, v in stocks.items() if k != "Gudang Online"]
    
    # Jika ada cabang lain yang masih "Tersedia" melimpah -> Jangan dilaporkan
    if "Tersedia" in branch_statuses:
        return False

    all_empty = all(s in ["Kosong", "On Restock"] for s in branch_statuses)
    
    # Jika Gudang Online & SEMUA Cabang Kosong -> Laporkan!
    if all_empty:
        return True
        
    # Cek cabang yang sisa stoknya di bawah 11 pcs
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
# PROSES ALL SKU & NOTIFIKASI OTOMATIS
# ==========================================
def process_all_skus(chat_id=None, is_auto=False):
    try:
        skus = load_skus()
        if not skus:
            if chat_id:
                bot.send_message(chat_id, "⚠️ File CSV SKU kosong atau tidak ditemukan.")
            return

        if chat_id and not is_auto:
            bot.send_message(chat_id, f"⌛ Memulai pengecekan {len(skus)} SKU...\nFilter: Menampilkan SKU Kosong Total & Stok < 11 pcs.")

        filtered_report = {}
        not_matched = []

        for sku in skus:
            try:
                res = check_single_sku_from_jaknot(sku)
                if res == "NOT_FOUND" or res is None:
                    not_matched.append(sku)
                else:
                    if should_report_sku(res):
                        filtered_report[sku] = res
            except Exception:
                not_matched.append(sku)
            time.sleep(random.randint(3, 5))

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if not filtered_report and not not_matched:
            msg = f"📊 **LAPORAN STOK JAKNOT**\n{now}\n\n✅ **Semua SKU Aman!** Tidak ada SKU yang kosong total atau menipis (<11 pcs)."
        else:
            msg = f"📊 **LAPORAN STOK KRITIS & KOSONG**\n{now}\n\n"
            for sku, stocks in filtered_report.items():
                msg += f"🔹 **SKU: {sku}**\n"
                for loc, status in stocks.items():
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
    """Thread penjadwalan otomatis setiap 1 jam"""
    while True:
        if is_auto_active:
            print("[INFO] Jalankan Pengecekan Otomatis (Filtered)...")
            process_all_skus(is_auto=True)
        time.sleep(3600)

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
        "👋 Selamat datang di Bot Pantau Stok Jaknot!\nID Chat Anda berhasil terdaftar untuk menerima laporan otomatis per jam.\n\nPilih menu di bawah ini:",
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
        bot.answer_callback_query(call.id, f"Auto-Check 1 Jam {status_txt}")
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=get_main_keyboard())
        
    elif call.data == "bot_status":
        status_str = "🟢 AKTIF (Cek tiap 1 jam)" if is_auto_active else "🔴 NONAKTIF (Auto-check mati)"
        skus = load_skus()
        users = get_registered_users()
        msg_status = (
            f"📌 **STATUS BOT STOK JAKNOT**\n\n"
            f"• **Auto-Check 1 Jam:** {status_str}\n"
            f"• **Total SKU Dipantau:** {len(skus)} SKU\n"
            f"• **Penerima Laporan:** {len(users)} Chat ID\n"
            f"• **Filter Laporan:** Kosong & Stok < 11 Pcs\n"
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
        stock_result = check_single_sku_from_jaknot(text)
        
        if isinstance(stock_result, dict):
            msg = f"📌 *DETAIL STOK MANUAL SKU: {text}*\n\n"
            for loc, status in stock_result.items():
                icon = "✅" if status in ["Tersedia"] or "Sisa" in status else "❌"
                msg += f"• *{loc}:* {icon} {status}\n"
            bot.reply_to(message, msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
        elif stock_result == "NOT_FOUND":
            bot.reply_to(message, f"❌ SKU *{text}* tidak ditemukan/tidak match di Jaknot.", parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            bot.reply_to(message, f"⚠️ Gagal mengambil data stok untuk SKU *{text}*.", parse_mode="Markdown", reply_markup=get_main_keyboard())
        
        user_states[chat_id] = None

if __name__ == "__main__":
    print("[INFO] Bot Stok Jaknot Interaktif Siap...")
    threading.Thread(target=schedule_checker, daemon=True).start()
    bot.infinity_polling()
