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
TELEGRAM_TOKEN = "8671011621:AAF94MFymPkicZYOHqfD2nvPcqHnzwBC1N0"
CSV_FILE = "daftar_sku-v2.csv"

# Daftar Cabang Acuan Jaknot
LOCATIONS = [
    "Gudang Online", "Jakarta Barat", "Jakarta Pusat", 
    "Jakarta Utara", "Cikupa", "Tangerang"
]

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}

# ==========================================
# FUNGSI BACA & SIMPAN CSV SKU
# ==========================================
def load_skus():
    """Membaca daftar SKU dari file CSV"""
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
        stock_data = {loc: "Kosong" for loc in LOCATIONS}
        
        cards = prod_soup.select('div[class*="stock"], div[class*="store"], .border-rounded, div[class*="item"]')
        
        for card in cards:
            text = card.get_text(separator=' ', strip=True)
            text_lower = text.lower()
            
            for loc in LOCATIONS:
                if loc.lower() in text_lower:
                    import re
                    if "sisa" in text_lower:
                        nums = re.findall(r'\d+', text)
                        if nums:
                            stock_data[loc] = f"Sisa {nums[0]} pcs"
                        else:
                            stock_data[loc] = "Tersedia"
                    elif "tersedia" in text_lower or "ready" in text_lower:
                        stock_data[loc] = "Tersedia"
                    elif "kosong" in text_lower or "habis" in text_lower or "pre-order" in text_lower:
                        stock_data[loc] = "Kosong"
                    elif "pcs" in text_lower:
                        nums = re.findall(r'\d+', text)
                        if nums:
                            stock_data[loc] = f"{nums[0]} pcs"

        return stock_data

    except Exception as e:
        print(f"[ERROR Scrape {sku}]: {e}")
        return None

# ==========================================
# PROSES ALL SKU & NOTIFIKASI OTOMATIS
# ==========================================
def process_all_skus(chat_id=None):
    skus = load_skus()
    if not skus:
        if chat_id:
            bot.send_message(chat_id, "⚠️ File CSV SKU kosong atau tidak ditemukan.")
        return

    if chat_id:
        bot.send_message(chat_id, f"⌛ Memulai pengecekan {len(skus)} SKU...\nEstimasi delay 5–8 detik per SKU untuk keamanan IP.")

    report = {}
    not_matched = []

    for sku in skus:
        res = check_single_sku_from_jaknot(sku)
        if res == "NOT_FOUND" or res is None:
            not_matched.append(sku)
        else:
            report[sku] = res
        time.sleep(random.randint(5, 8))

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"📊 **LAPORAN STOK JAKNOT**\n{now}\n\n"

    for sku, stocks in report.items():
        msg += f"🔹 **SKU: {sku}**\n"
        for loc, status in stocks.items():
            icon = "✅" if status != "Kosong" else "❌"
            msg += f"  • {loc}: {icon} {status}\n"
        msg += "\n"

    if not_matched:
        msg += f"❓ **SKU TIDAK MATCH / DELETED:**\n" + ", ".join(not_matched)

    if chat_id:
        if len(msg) > 4000:
            for x in range(0, len(msg), 4000):
                bot.send_message(chat_id, msg[x:x+4000], parse_mode="Markdown")
        else:
            bot.send_message(chat_id, msg, parse_mode="Markdown")

def schedule_checker():
    """Thread penjadwalan otomatis setiap 1 jam"""
    while True:
        process_all_skus()
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
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "👋 Selamat datang di Bot Pantau Stok Jaknot!\nPilih menu di bawah ini:",
        reply_markup=get_main_keyboard()
    )

@bot.callback_query_handler(func=lambda call: True)
def callback_listener(call):
    chat_id = call.message.chat.id
    if call.data == "check_now":
        bot.answer_callback_query(call.id, "Memulai pengecekan...")
        threading.Thread(target=process_all_skus, args=(chat_id,)).start()
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
                icon = "❌" if status == "Kosong" else "✅"
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
