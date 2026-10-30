import asyncio
import os
import re
import threading
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urljoin, urldefrag
import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ============================================================
# 🌐 WEB SERVER DUMMY CHO RENDER
# ============================================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("Bot WordPress & Leosansutu đang hoạt động!".encode('utf-8'))

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

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

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")        
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "") 
KINDLE_EMAIL = os.getenv("KINDLE_EMAIL", "")        

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://www.google.com/"
}

session = requests.Session()
session.headers.update(HEADERS)

def get_content(url):
    try:
        res = session.get(url, timeout=25)
        if res.status_code == 200:
            return BeautifulSoup(res.text, "lxml")
    except Exception as e:
        print(f"Lỗi tải {url}: {e}")
    return None

def get_chapters(url):
    soup = get_content(url)
    if not soup: return [], "Truyện", None
    
    title_el = soup.select_one("h1.entry-title") or soup.select_one("h1") or soup.title
    title = title_el.get_text().strip() if title_el else "Truyện WordPress"
    if "|" in title:
        title = title.split('|')[0].strip()
        
    cover_url = None
    for meta_prop in ["og:image", "twitter:image", "image"]:
        meta_img = soup.find("meta", property=meta_prop) or soup.find("meta", attrs={"name": meta_prop})
        if meta_img and meta_img.get("content"):
            cover_url = meta_img["content"]
            break
            
    if not cover_url:
        content_area = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article") or soup.body
        if content_area:
            first_img = content_area.find("img")
            if first_img and first_img.get("src"):
                cover_url = first_img["src"]
                
    if not cover_url:
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]) and not any(w in src.lower() for w in ["avatar", "icon", "logo", "emoji"]):
                cover_url = src
                break

    if cover_url:
        cover_url = urljoin(url, cover_url)

    content_area = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article") or soup.body

    chapters = []
    if content_area:
        for a in content_area.find_all("a", href=True):
            href = urldefrag(urljoin(url, a.get("href")))[0]
            text = a.get_text().strip()
            
            is_chapter = re.match(r"^(\d+|chuong|chương|hồi|hoi|quyển|quyen|phần|phan|ngoại truyện|ngoai truyen|pn|nt|c\s*\d+)", text, flags=re.IGNORECASE)
            
            if is_chapter and len(text) < 100: 
                if not any(c['url'] == href for c in chapters):
                    chapters.append({"name": text, "url": href})
                    
    return chapters, title, cover_url

def download_chap(url):
    soup = get_content(url)
    if not soup: return None, []
    
    content = soup.select_one(".entry-content") or soup.select_one(".post-content") or soup.select_one("article")
    if not content: return None, []
    
    # Gộp các trang con của WordPress (nếu bài viết bị phân trang thành nhiều trang)
    pagin_container = soup.select_one(".page-links") or soup.select_one(".post-pagination") or soup.select_one(".pagination")
    if pagin_container:
        seen_urls = {url}
        for a in pagin_container.find_all("a", href=True):
            p_url = urljoin(url, a.get("href"))
            if p_url not in seen_urls and "#" not in p_url:
                seen_urls.add(p_url)
                p_soup = get_content(p_url)
                if p_soup:
                    p_content = p_soup.select_one(".entry-content") or p_soup.select_one(".post-content") or p_soup.select_one("article")
                    if p_content:
                        for t in p_content.select("script, style, ins, button, iframe, form, nav, comments, .sharedaddy, .jp-relatedposts, .social-share, .post-navigation, .relate-post"):
                            t.decompose()
                        for element in list(p_content.children):
                            content.append(element)

    # Dọn dẹp rác hệ thống kỹ thuật thuần túy
    trash_selectors = [
        "script", "style", "ins", "button", "iframe", "form", "nav", "comments",
        ".sharedaddy", ".jp-relatedposts", ".social-share", ".post-navigation", ".relate-post", ".page-links", ".post-pagination"
    ]
    for t in content.select(", ".join(trash_selectors)):
        t.decompose()
            
    local_images = []
    img_counter = 0
    for img in content.find_all("img"):
        img_src = img.get("src")
        if not img_src:
            img.decompose()
            continue
        
        full_img_url = urljoin(url, img_src)
        try:
            img_res = session.get(full_img_url, timeout=10)
            if img_res.status_code == 200:
                img_filename = f"images/chap_img_{id(img)}_{img_counter}.jpg"
                local_images.append({
                    "file_name": img_filename,
                    "content": img_res.content
                })
                img['src'] = img_filename
                for attr in list(img.attrs):
                    if attr not in ['src', 'alt', 'title']:
                        del img[attr]
                img_counter += 1
            else:
                img.decompose()
        except:
            img.decompose()

    return str(content), local_images

def send_to_kindle_email(file_path, title):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not KINDLE_EMAIL:
        return False, "Chưa cấu hình thông tin Email trên Render."
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = KINDLE_EMAIL
        msg['Subject'] = f"Convert {title}"

        msg.attach(MIMEText("Gửi file EPUB từ Bot tự động."))

        with open(file_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(file_path))
            part.add_header('Content-Disposition', 'attachment', filename=os.path.basename(file_path))
            msg.attach(part)

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, KINDLE_EMAIL, msg.as_string())
        server.quit()
        return True, "Đã gửi thành công vào Kindle!"
    except Exception as e:
        return False, f"Lỗi gửi Kindle: {str(e)}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = re.findall(r"https?://[^\s]+", update.message.text or "")
    if not url: return
    
    status = await update.message.reply_text("⏳ Đang phân tích trang WordPress/Leosansutu...")
    chapters, title, cover_url = get_chapters(url[0])
    
    if not chapters:
        await status.edit_text("❌ Không tìm thấy chương nào. Hãy chắc chắn bạn gửi link trang mục lục hoặc bài tổng hợp truyện.")
        return

    await status.edit_text(f"📚 {title}\n✅ Tìm thấy {len(chapters)} chương. Đang tải dữ liệu & xử lý ảnh...")

    results = {}
    for i, c in enumerate(chapters):
        results[i] = download_chap(c["url"])
        if i % 10 == 0 or i == len(chapters) - 1:
            pct = int(((i + 1) / len(chapters)) * 100)
            try:
                await status.edit_text(f"📚 {title}\n⏳ Đang tải nội dung: {pct}%\n({i+1}/{len(chapters)})")
            except:
                pass

    book = epub.EpubBook()
    book.set_identifier('wp_' + re.sub(r'\W+', '', title))
    book.set_title(title)
    book.set_language('vi')

    if cover_url:
        try:
            print(f"Đang tải ảnh bìa từ: {cover_url}")
            img_res = session.get(cover_url, timeout=15)
            if img_res.status_code == 200:
                book.set_cover("cover.jpg", img_res.content)
                print("Đã thiết lập ảnh bìa thành công!")
            else:
                print(f"Không thể tải ảnh bìa, mã lỗi HTTP: {img_res.status_code}")
        except Exception as e:
            print(f"Lỗi ngoại lệ khi tải ảnh bìa: {e}")

    chapters_list = []
    for i, c in enumerate(chapters):
        chap_data = results.get(i)
        if chap_data and chap_data[0]:
            content_html, local_images = chap_data
            
            for img_info in local_images:
                img_item = epub.EpubItem(
                    file_name=img_info["file_name"],
                    media_type="image/jpeg",
                    content=img_info["content"]
                )
                book.add_item(img_item)

            chap = epub.EpubHtml(title=c["name"], file_name=f"chap_{i+1}.xhtml")
            chap.content = f"<h2>{c['name']}</h2>{content_html}"
            book.add_item(chap)
            chapters_list.append(chap)

    if not chapters_list:
        await status.edit_text("❌ Không tải được nội dung chương nào.")
        return

    book.toc = tuple(chapters_list)
    book.spine = ['nav'] + chapters_list

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Truyen_WP"
    file_out = f"{safe_title}.epub"
    epub.write_epub(file_out, book)

    await status.edit_text(f"⬆️ Đang gửi file EPUB...")
    await update.message.reply_document(
        document=open(file_out, "rb"), 
        caption=f"✅ Hoàn tất: {title}\n📖 Trọn bộ {len(chapters_list)} chương + Ảnh bìa & Nhúng ảnh minh họa đầy đủ!"
    )
    
    if KINDLE_EMAIL and SENDER_EMAIL:
        success, msg_note = send_to_kindle_email(file_out, title)
        await update.message.reply_text(f"📧 Trạng thái gửi Kindle: {msg_note}")

    await status.delete()
    if os.path.exists(file_out):
        os.remove(file_out)

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    
    if not BOT_TOKEN:
        print("❌ Lỗi: Thiếu BOT_TOKEN trong Environment Variables!")
        return
        
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
