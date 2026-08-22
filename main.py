import asyncio
import os
import re
import threading
from urllib.parse import urljoin, urldefrag
from bs4 import BeautifulSoup
from ebooklib import epub
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
import requests

# --- CẤU HÌNH ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
session = requests.Session()
session.headers.update(HEADERS)

def get_content(url):
    try:
        res = session.get(url, timeout=15)
        return BeautifulSoup(res.text, "lxml")
    except: return None

# --- XỬ LÝ WORDPRESS ---
def get_chapters(url):
    soup = get_content(url)
    if not soup: return [], "Truyện"
    
    # Lấy tiêu đề
    title = soup.title.string.split('|')[0].strip() if soup.title else "Truyện WordPress"
    
    # Tìm vùng chứa chương (WordPress thường dùng entry-content hoặc post-content)
    content_area = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article")
    
    chapters = []
    if content_area:
        for a in content_area.find_all("a", href=True):
            href = urldefrag(urljoin(url, a.get("href")))[0]
            text = a.get_text().strip()
            # Lọc link chương: Phải có từ khoá chương/hồi hoặc định dạng C+số
            if re.match(r"^(chương|chuong|hồi|quyển|c\s*\d+)", text, flags=re.I):
                chapters.append({"name": text, "url": href})
    return chapters, title

def download_chap(url):
    soup = get_content(url)
    if not soup: return None
    # Tìm vùng nội dung chính
    content = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article")
    if not content: return None
    # Xoá rác
    for t in content.find_all(["script", "style", "ins", "a", "button"]): t.decompose()
    return str(content)

# --- TELEGRAM HANDLER ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang quét danh sách chương WordPress...")
    chapters, title = get_chapters(url[0])
    
    if not chapters:
        await status.edit_text("❌ Không tìm thấy chương nào. Hãy gửi link bài viết tổng hợp truyện.")
        return

    await status.edit_text(f"📚 {title}\n✅ Tìm thấy {len(chapters)} chương. Đang tải...")

    # Tải nội dung
    results = {}
    for i, c in enumerate(chapters):
        results[i] = download_chap(c["url"])
        if i % 10 == 0:
            pct = int((i / len(chapters)) * 100)
            await status.edit_text(f"📚 {title}\n⏳ Đang tải: {pct}%...")

    # Tạo EPUB
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
    file_out = "truyen_wp.epub"
    epub.write_epub(file_out, book)
    
    await update.message.reply_document(document=open(file_out, "rb"), caption=f"✅ Xong! {title}")
    await status.delete()

if __name__ == "__main__":
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
