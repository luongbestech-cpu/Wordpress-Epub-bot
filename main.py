import asyncio
import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urljoin, urldefrag
from bs4 import BeautifulSoup
from ebooklib import epub
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
import requests

# ============================================================
# 🌐 WEB SERVER DUMMY CHO RENDER (BẮT BUỘC PHẢI CÓ)
# ============================================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bot WordPress đang hoạt động!".encode('utf-8'))

    def log_message(self, format, *args):
        return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# ============================================================
# ⚙️ CẤU HÌNH WORDPRESS BOT
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
session = requests.Session()
session.headers.update(HEADERS)

def get_content(url):
    try:
        res = session.get(url, timeout=15)
        if res.status_code == 200:
            return BeautifulSoup(res.text, "lxml")
    except: 
        pass
    return None

def get_chapters(url):
    soup = get_content(url)
    if not soup: return [], "Truyện"
    
    title_el = soup.select_one("h1") or soup.title
    title = title_el.get_text().strip() if title_el else "Truyện WordPress"
    if "|" in title:
        title = title.split('|')[0].strip()
    
    content_area = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article")
    
    chapters = []
    if content_area:
        for a in content_area.find_all("a", href=True):
            href = urldefrag(urljoin(url, a.get("href")))[0]
            text = a.get_text().strip()
            if re.match(r"^(chương|chuong|hồi|quyển|c\s*\d+)", text, flags=re.I):
                # Tránh trùng lặp link
                if not any(c['url'] == href for c in chapters):
                    chapters.append({"name": text, "url": href})
    return chapters, title

def download_chap(url):
    soup = get_content(url)
    if not soup: return None
    content = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article")
    if not content: return None
    for t in content.find_all(["script", "style", "ins", "a", "button"]): 
        t.decompose()
    return str(content)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang quét danh sách chương WordPress...")
    chapters, title = get_chapters(url[0])
    
    if not chapters:
        await status.edit_text("❌ Không tìm thấy chương nào. Hãy gửi link bài viết tổng hợp truyện.")
        return

    await status.edit_text(f"📚 {title}\n✅ Tìm thấy {len(chapters)} chương. Đang tải...")

    results = {}
    for i, c in enumerate(chapters):
        results[i] = download_chap(c["url"])
        if i % 10 == 0 or i == len(chapters) - 1:
            pct = int((i / len(chapters)) * 100)
            try:
                await status.edit_text(f"📚 {title}\n⏳ Đang tải: {pct}%\n({i+1}/{len(chapters)})")
            except:
                pass

    book = epub.EpubBook()
    book.set_title(title)
    for i, c in enumerate(chapters):
        if results.get(i):
            chap = epub.EpubHtml(title=c["name"], file_name=f"c{i}.xhtml")
            chap.content = f"<h2>{c['name']}</h2>{results[i]}"
            book.add_item(chap)
            book.spine.append(chap)
    
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    file_out = (re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen_WP") + ".epub"
    epub.write_epub(file_out, book)
    
    await update.message.reply_document(document=open(file_out, "rb"), caption=f"✅ Xong! {title}")
    await status.delete()
    if os.path.exists(file_out):
        os.remove(file_out)

def main():
    # Khởi động Web Server ngầm để Render nhận diện cổng mạng
    threading.Thread(target=run_web_server, daemon=True).start()
    
    if not BOT_TOKEN:
        print("❌ Thiếu BOT_TOKEN!")
        return
        
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
