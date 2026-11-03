import os
import json
import logging
import asyncio
from telegram import Update, Poll
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import google.generativeai as genai

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = "9150760320:AAFu9qP6af_GPinpXPpfNvEDnvQGTpyXKCs"
GEMINI_API_KEY = "AQ.Ab8RN6Jg-G9Pb1Z4yub0hJVHTIFCqAe7DsaN4rOX2-Du4reEJw"

genai.configure(api_key=GEMINI_API_KEY)
# Gemini 1.5 Flash natively supports Text, Images, and PDFs
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
        # Highest resolution photo download karein
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
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
        pdf_file = await doc.get_file()
        pdf_bytes = await pdf_file.download_as_bytearray()

        pdf_data = {
            "mime_type": "application/pdf",
            "data": bytes(pdf_bytes)
        }

        # Gemini se structured JSON format me Quiz mangwana
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

        # Force JSON output for 100% reliability
        response = model.generate_content(
            [prompt, pdf_data],
            generation_config={"response_mime_type": "application/json"}
        )

        quiz_list = json.loads(response.text)

        await update.message.reply_text(f"✅ DPP se {len(quiz_list)} Questions taiyar kiye gaye hain! Solve karein:")

        for q in quiz_list:
            # Telegram character limits safety checks
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
                await asyncio.sleep(1) # Spam prevention delay

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

# ================= MAIN RUNNER =================
def main():
    print("Bot start ho raha hai...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))

    # Handlers for Text, Photo, and Document (PDF)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    print("Bot chalu hai! Ab aap Text, Photo ya PDF bhej sakte hain.")
    app.run_polling()

if __name__ == "__main__":
    main()
