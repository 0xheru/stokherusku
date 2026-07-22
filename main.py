import os
import csv
import time
import random
import datetime
import threading
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

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
    Mengambil data stok 1 SKU dari API/Web Jaknot.
    Mengembalikan dict cabang & stok, atau None jika tidak match/deleted.
    """
    url = f"https://www.jakartanotebook.com/api/product/detail/{sku}" # Disesuaikan dengan endpoint API/web Jaknot
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Asumsi struktur JSON Jaknot (sesuaikan jika ada perbedaan key)
            stock_data = data.get('stocks', {}) 
            return stock_data
        elif response.status_code == 404:
            return "NOT_FOUND"
        else:
            return None
    except Exception:
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
