import asyncio
import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urljoin, urldefrag
import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ============================================================
# 🌐 WEB SERVER DUMMY CHO RENDER (GIỮ APP KHÔNG BỊ CRASH)
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
# ⚙️ CẤU HÌNH BOT & PHIÊN LÀM VIỆC
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN_WORDPRESS") or os.getenv("BOT_TOKEN")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

session = requests.Session()
session.headers.update(HEADERS)

def get_content(url):
    try:
        res = session.get(url, timeout=20)
        if res.status_code == 200:
            return BeautifulSoup(res.text, "lxml")
    except Exception as e:
        print(f"Lỗi tải {url}: {e}")
    return None

def get_chapters(url):
    soup = get_content(url)
    if not soup: return [], "Truyện", None
    
    # Lấy tiêu đề truyện
    title_el = soup.select_one("h1") or soup.title
    title = title_el.get_text().strip() if title_el else "Truyện WordPress"
    if "|" in title:
        title = title.split('|')[0].strip()
        
    # Lấy ảnh bìa (ưu tiên og:image hoặc ảnh đầu tiên trong bài)
    cover_url = None
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        cover_url = og_img["content"]
        
    content_area = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article") or soup.body
    
    if not cover_url and content_area:
        first_img = content_area.find("img")
        if first_img and first_img.get("src"):
            cover_url = first_img["src"]

    chapters = []
    if content_area:
        for a in content_area.find_all("a", href=True):
            href = urldefrag(urljoin(url, a.get("href")))[0]
            text = a.get_text().strip()
            
            # Bộ lọc thông minh: nhận diện Số (01, 1), PN, NT (Ngoại truyện), Chương, Hồi...
            is_chapter = re.match(r"^(\d+|PN\s*\d+|NT\s*\d+|chương|chuong|hồi|hoi|quyển|quyen|c\s*\d+)", text, flags=re.IGNORECASE)
            
            if is_chapter:
                if not any(c['url'] == href for c in chapters):
                    chapters.append({"name": text, "url": href})
                    
    return chapters, title, cover_url

def download_chap(url):
    soup = get_content(url)
    if not soup: return None
    content = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article")
    if not content: return None
    
    # Xóa quảng cáo và các nút chức năng thừa
    for t in content.find_all(["script", "style", "ins", "button", "iframe", "form"]): 
        t.decompose()
        
    return str(content)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang quét danh sách chương WordPress...")
    chapters, title, cover_url = get_chapters(url[0])
    
    if not chapters:
        await status.edit_text("❌ Không tìm thấy chương nào. Hãy kiểm tra lại đường dẫn bài viết tổng hợp.")
        return

    await status.edit_text(f"📚 {title}\n✅ Tìm thấy {len(chapters)} chương. Đang tiến hành tải...")

    # Tải nội dung từng chương
    results = {}
    for i, c in enumerate(chapters):
        results[i] = download_chap(c["url"])
        if i % 10 == 0 or i == len(chapters) - 1:
            pct = int(((i + 1) / len(chapters)) * 100)
            try:
                await status.edit_text(f"📚 {title}\n⏳ Đang tải nội dung: {pct}%\n({i+1}/{len(chapters)})")
            except:
                pass

    # Tạo file EPUB chuẩn Kindle
    book = epub.EpubBook()
    book.set_identifier('wp_' + re.sub(r'\W+', '', title))
    book.set_title(title)
    book.set_language('vi')

    # Thêm ảnh bìa (Cover Image)
    if cover_url:
        try:
            img_res = session.get(cover_url, timeout=15)
            if img_res.status_code == 200:
                book.set_cover("cover.jpg", img_res.content)
        except Exception as e:
            print(f"Lỗi tải ảnh bìa: {e}")

    # Thêm nội dung các chương
    chapters_list = []
    for i, c in enumerate(chapters):
        if results.get(i):
            chap = epub.EpubHtml(title=c["name"], file_name=f"chap_{i+1}.xhtml")
            chap.content = f"<h2>{c['name']}</h2>{results[i]}"
            book.add_item(chap)
            chapters_list.append(chap)

    if not chapters_list:
        await status.edit_text("❌ Không tải được nội dung chương nào.")
        return

    # Cấu hình Mục lục (TOC) & Luồng đọc (Spine)
    book.toc = tuple(chapters_list)
    book.spine = ['nav'] + chapters_list

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Xuất file
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen_WP"
    file_out = f"{safe_title}.epub"
    epub.write_epub(file_out, book)

    # Gửi file cho người dùng qua Telegram
    await status.edit_text(f"⬆️ Đang gửi file EPUB...")
    with open(file_out, "rb") as f:
        await update.message.reply_document(
            document=f, 
            caption=f"✅ Hoàn tất: {title}\n📖 Trọn bộ {len(chapters_list)} chương + Ảnh bìa & Mục lục chuẩn Kindle!"
        )
    
    await status.delete()
    if os.path.exists(file_out):
        os.remove(file_out)

def main():
    # Khởi động HTTP Server ngầm cho Render
    threading.Thread(target=run_web_server, daemon=True).start()
    
    if not BOT_TOKEN:
        print("❌ Lỗi: Thiếu BOT_TOKEN trong Environment Variables!")
        return
        
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
