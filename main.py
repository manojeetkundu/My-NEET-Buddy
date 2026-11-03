import os
import json
import logging
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import google.generativeai as genai

# ================= CONFIGURATION (SECURE) =================
# Keys ab Environment Variables se load hongi
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("ERROR: TELEGRAM_BOT_TOKEN ya GEMINI_API_KEY environment variable set nahi hai!")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Long messages safe split (Telegram limit 4096 chars)
async def send_safe_message(update: Update, text: str):
    if not text:
        await update.message.reply_text("Maaf kijiye, koi answer generate nahi ho paya.")
        return
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000])

# ================= 1. /start COMMAND =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🩺 **NEET Super Assistant Bot me aapka swagat hai!**\n\n"
        "✨ **Main kya-kya kar sakta hoon:**\n"
        "1. 💬 **Text Doubt:** Sawal type karke bhejein.\n"
        "2. 📸 **Photo Doubt:** Kisi bhi question paper ya book ki photo bhejein.\n"
        "3. 📄 **DPP to Quiz:** Apni DPP ya Notes ki **PDF file** bhejein, main turant uske MCQs ka Telegram Quiz bana dunga!\n"
        "4. 📝 **/quiz:** General NEET practice quiz ke liye."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# ================= 2. PHOTO DOUBT SOLVER =================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Photo analyze ho rahi hai, kripya thoda intezar karein...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        photo = update.message.photo[-1]
        # Download timeout 60s taaki connection break na ho
        photo_file = await photo.get_file(read_timeout=60)
        photo_bytes = await photo_file.download_as_bytearray()

        caption = update.message.caption or "Solve this NEET question step by step in simple Hinglish."
        prompt = (
            f"You are a top NEET Tutor (Physics, Chemistry, Biology). "
            f"Read the image carefully and answer the question: {caption}"
        )

        image_data = {
            "mime_type": "image/jpeg",
            "data": bytes(photo_bytes)
        }

        response = model.generate_content([prompt, image_data])
        await send_safe_message(update, response.text)

    except Exception as e:
        logging.error(f"Photo Error: {e}")
        await update.message.reply_text("⚠️ Photo process karne me error aaya. Kripya photo clear bhej kar dobara try karein.")

# ================= 3. PDF (DPP) TO QUIZ GENERATOR =================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    
    # Check if file is PDF
    if not (doc.file_name.lower().endswith(".pdf") or doc.mime_type == "application/pdf"):
        await update.message.reply_text("⚠️ Kripya sirf PDF file bhejein.")
        return

    await update.message.reply_text("⏳ DPP PDF padhi ja rahi hai... Quiz generate ho raha hai!")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Download timeout 60s for PDF
        pdf_file = await doc.get_file(read_timeout=60)
        pdf_bytes = await pdf_file.download_as_bytearray()

        pdf_data = {
            "mime_type": "application/pdf",
            "data": bytes(pdf_bytes)
        }

        prompt = (
            "You are a NEET Exam question extractor. Analyze this DPP/Document and create 3 to 5 NEET-level MCQs from it. "
            "Return strictly a JSON array of objects with this schema:\n"
            "[\n"
            "  {\n"
            "    \"question\": \"Question text (keep under 250 characters)\",\n"
            "    \"options\": [\"Option A\", \"Option B\", \"Option C\", \"Option D\"],\n"
            "    \"correct_id\": 0,\n"
            "    \"explanation\": \"Brief NCERT explanation (under 180 characters)\"\n"
            "  }\n"
            "]\n"
            "Important: Do not exceed 90 characters for any option. correct_id must be integer (0 to 3)."
        )

        response = model.generate_content(
            [prompt, pdf_data],
            generation_config={"response_mime_type": "application/json"}
        )

        quiz_list = json.loads(response.text)

        await update.message.reply_text(f"✅ DPP se {len(quiz_list)} Questions taiyar kiye gaye hain! Solve karein:")

        for q in quiz_list:
            question_text = q.get("question", "")[:290]
            options = [opt[:95] for opt in q.get("options", [])[:4]]
            correct_id = int(q.get("correct_id", 0))
            explanation = q.get("explanation", "")[:190]

            if len(options) >= 2:
                await context.bot.send_poll(
                    chat_id=update.effective_chat.id,
                    question=question_text,
                    options=options,
                    type=Poll.QUIZ,
                    correct_option_id=correct_id,
                    explanation=explanation,
                    is_anonymous=False
                )
                await asyncio.sleep(1)

    except Exception as e:
        logging.error(f"PDF Quiz Error: {e}")
        await update.message.reply_text("⚠️ PDF se quiz generate karne me error aaya. Check karein ki PDF readable hai.")

# ================= 4. TEXT DOUBT SOLVER =================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_query = update.message.text
    if not user_query:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    prompt = (
        "You are an expert NEET tutor for Physics, Chemistry, and Biology. "
        "Explain step-by-step in simple Hinglish:\n\n" + user_query
    )

    try:
        response = model.generate_content(prompt)
        await send_safe_message(update, response.text)
    except Exception as e:
        logging.error(f"Text Error: {e}")
        await update.message.reply_text("⚠️ Doubt solve karne me error aaya. Kripya dobara try karein.")

# ================= MAIN RUNNER (TIMEOUTS FIXED) =================
def main():
    print("Bot start ho raha hai...")

    # Network timeouts 30 seconds set kiye gaye hain taaki Render pe TimedOut error na aaye
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    print("Bot chalu hai! Listening for updates...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
    # Render port check pass karne ke liye dummy server
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Alive and Running!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()
    def main():
    print("Bot start ho raha hai...")

    # Dummy Web Server ko background thread me run karein taaki Render khush rahe
    threading.Thread(target=run_dummy_server, daemon=True).start()

    # Network timeouts
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    print("Bot chalu hai! Listening for updates...")
    app.run_polling(drop_pending_updates=True)
