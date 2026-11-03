import os
import json
import logging
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, Poll
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import google.generativeai as genai

# ================= CONFIGURATION & ENV VARIABLES =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("CRITICAL: TELEGRAM_BOT_TOKEN aur GEMINI_API_KEY environment variables set nahi hain!")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ================= DUMMY SERVER (RENDER FIX) =================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is healthy and running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        # Console me dummy server ke logs ko suppress karne ke liye
        return

def start_dummy_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logging.info(f"Dummy health check server running on port {port}")
    server.serve_forever()

# ================= HELPER FUNCTIONS =================
async def send_safe_message(update: Update, text: str):
    if not text:
        await update.message.reply_text("Maaf kijiye, koi answer generate nahi ho paya.")
        return
    # Telegram message limit: 4096 characters
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000])

# ================= 1. /start COMMAND =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🩺 *NEET Super Assistant Bot me aapka swagat hai!*\n\n"
        "✨ *Features:*\n"
        "1. 💬 *Text Doubt:* Sawal type karke bhejein.\n"
        "2. 📸 *Photo Doubt:* Kisi question paper ya book ki photo bhejein.\n"
        "3. 📄 *DPP to Quiz:* Apni DPP ya Notes ki *PDF file* bhejein, main turant MCQs Quiz bana dunga!\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# ================= 2. PHOTO DOUBT SOLVER =================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Photo analyze ho rahi hai, kripya thoda intezar karein...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        photo_bytes = await photo_file.download_as_bytearray()

        caption = update.message.caption or "Solve this NEET question step by step in simple Hinglish."
        prompt = (
            "You are a top NEET Tutor (Physics, Chemistry, Biology). "
            f"Read the image carefully and solve step by step: {caption}"
        )

        image_data = {
            "mime_type": "image/jpeg",
            "data": bytes(photo_bytes)
        }

        # Non-blocking async call
        response = await model.generate_content_async([prompt, image_data])
        await send_safe_message(update, response.text)

    except Exception as e:
        logging.error(f"Photo Error: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Photo process karne me error aaya. Photo clear bhej kar dobara try karein.")

# ================= 3. PDF (DPP) TO QUIZ GENERATOR =================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    
    # File validation
    if not (doc.file_name and doc.file_name.lower().endswith(".pdf") or doc.mime_type == "application/pdf"):
        await update.message.reply_text("⚠️ Kripya sirf PDF format me file bhejein.")
        return

    # Free tier RAM OOM protection (restrict file size > 15MB)
    if doc.file_size and doc.file_size > 15 * 1024 * 1024:
        await update.message.reply_text("⚠️ PDF size bahut bada hai. Kripya 15MB se chhoti PDF bhejein.")
        return

    await update.message.reply_text("⏳ DPP PDF analyze ho rahi hai... Quiz taiyar ho raha hai!")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        pdf_file = await doc.get_file()
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
            "    \"question\": \"Question text (under 250 chars)\",\n"
            "    \"options\": [\"Option A\", \"Option B\", \"Option C\", \"Option D\"],\n"
            "    \"correct_id\": 0,\n"
            "    \"explanation\": \"Brief NCERT explanation (under 180 chars)\"\n"
            "  }\n"
            "]\n"
            "Strict Rules: Each option under 90 chars. correct_id must be integer between 0 and 3."
        )

        response = await model.generate_content_async(
            [prompt, pdf_data],
            generation_config={"response_mime_type": "application/json"}
        )

        cleaned_text = response.text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text[3:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]

        quiz_list = json.loads(cleaned_text.strip())

        if not isinstance(quiz_list, list) or len(quiz_list) == 0:
            await update.message.reply_text("⚠️ PDF se questions extract nahi ho sake.")
            return

        await update.message.reply_text(f"✅ DPP se {len(quiz_list)} Questions taiyar kiye gaye:")

        for q in quiz_list:
            question_text = str(q.get("question", ""))[:290]
            raw_options = q.get("options", [])
            options = [str(opt)[:95] for opt in raw_options][:4]
            explanation = str(q.get("explanation", ""))[:190]
            
            try:
                correct_id = int(q.get("correct_id", 0))
            except (ValueError, TypeError):
                correct_id = 0

            # Telegram Quiz limits check:
            # - At least 2 options required
            # - correct_option_id must be within range of options
            if len(options) >= 2:
                if correct_id < 0 or correct_id >= len(options):
                    correct_id = 0

                await context.bot.send_poll(
                    chat_id=update.effective_chat.id,
                    question=question_text,
                    options=options,
                    type=Poll.QUIZ,
                    correct_option_id=correct_id,
                    explanation=explanation,
                    is_anonymous=False
                )
                await asyncio.sleep(1.2)  # Prevent Telegram rate limits

    except Exception as e:
        logging.error(f"PDF Quiz Error: {e}", exc_info=True)
        await update.message.reply_text("⚠️ PDF se quiz generate karne me error aaya. Kripya check karein ki PDF readable text contain karti hai.")

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
        response = await model.generate_content_async(prompt)
        await send_safe_message(update, response.text)
    except Exception as e:
        logging.error(f"Text Error: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Doubt solve karne me error aaya. Kripya dobara try karein.")

# ================= MAIN RUNNER =================
def main():
    # 1. Start Dummy Web Server in a background thread for Render
    server_thread = threading.Thread(target=start_dummy_server, daemon=True)
    server_thread.start()

    # 2. Build Telegram Bot
    logging.info("Bot initializing...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    logging.info("Bot is polling updates...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
