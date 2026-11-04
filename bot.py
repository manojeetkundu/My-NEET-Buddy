import os
import io
import json
import base64
import asyncio
import logging
import threading
from typing import Optional

from flask import Flask, jsonify
from openai import OpenAI
from pypdf import PdfReader
from docx import Document

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)

# --------------------------------------------------
# Configuration
# --------------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")

AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MAX_DOCUMENT_CHARS = 25000
QUIZ_QUESTION_COUNT = 5

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

web_app = Flask(__name__)


# --------------------------------------------------
# Flask health endpoints for Render
# --------------------------------------------------

@web_app.get("/")
def home():
    return jsonify({"service": "NEET Sathi", "status": "running"}), 200


@web_app.get("/health")
def health():
    return jsonify({"status": "healthy"}), 200


def run_web_server():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)


# --------------------------------------------------
# OpenAI helpers
# --------------------------------------------------

def call_openai_text(prompt: str, instructions: str) -> str:
    if not client:
        raise RuntimeError("OPENAI_API_KEY is missing")

    response = client.responses.create(
        model=AI_MODEL,
        instructions=instructions,
        input=prompt,
    )
    return response.output_text.strip()


async def ask_ai(prompt: str, instructions: str) -> str:
    return await asyncio.to_thread(call_openai_text, prompt, instructions)


def call_openai_image(data_url: str, question: str) -> str:
    if not client:
        raise RuntimeError("OPENAI_API_KEY is missing")

    response = client.responses.create(
        model=AI_MODEL,
        instructions=(
            "You are NEET Sathi, a helpful NEET UG tutor. "
            "Solve the question visible in the image. Explain clearly "
            "in simple Hindi or Hinglish. Do not invent unreadable details."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": question or "Please solve the question in this image.",
                    },
                    {
                        "type": "input_image",
                        "image_url": data_url,
                    },
                ],
            }
        ],
    )
    return response.output_text.strip()


# --------------------------------------------------
# General commands
# --------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Namaste! 👋 Main NEET Sathi hoon.\n\n"
        "Mujhse NEET ke doubts poochho, image bhejo, "
        "topic quiz banao ya apne notes se quiz generate karo.\n\n"
        "Commands:\n"
        "/help - Help\n"
        "/quiz <topic> - Topic quiz\n"
        "/progress - Apni progress\n"
        "/cancel - Current quiz cancel"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "NEET Sathi ka use:\n\n"
        "• Text mein apna doubt bhejo\n"
        "• Question ki photo bhejo\n"
        "• /quiz Human Physiology - topic quiz\n"
        "• PDF, DOCX ya TXT file bhejo - document quiz\n"
        "• /progress - quiz progress\n"
        "• /cancel - current quiz cancel"
    )


async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    answered = data.get("total_answered", 0)
    correct = data.get("total_correct", 0)

    percentage = round((correct / answered) * 100) if answered else 0

    await update.effective_message.reply_text(
        "📊 Tumhari progress:\n"
        f"Answered: {answered}\n"
        f"Correct: {correct}\n"
        f"Accuracy: {percentage}%"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("active_quiz", None)
    context.user_data.pop("quiz_questions", None)
    context.user_data.pop("quiz_index", None)
    context.user_data.pop("quiz_score", None)
    context.user_data.pop("quiz_chat_id", None)

    await update.effective_message.reply_text("Current quiz cancel kar diya gaya hai.")


# --------------------------------------------------
# Text doubt solving
# --------------------------------------------------

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    await message.chat.send_action("typing")

    try:
        answer = await ask_ai(
            message.text,
            (
                "You are NEET Sathi, a supportive NEET UG tutor. "
                "Explain in simple Hindi or Hinglish. "
                "Be accurate, clear, and educational. "
                "For numerical questions, show the essential steps."
            ),
        )
        await message.reply_text(answer[:4000])

    except Exception:
        logger.exception("Text doubt request failed")
        await message.reply_text(
            "Abhi jawab dene mein dikkat aa rahi hai. "
            "Thodi der baad dobara try karo."
        )


# --------------------------------------------------
# Image doubt solving
# --------------------------------------------------

async def photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.photo:
        return

    if not client:
        await message.reply_text("OpenAI API key configured nahi hai.")
        return

    await message.chat.send_action("typing")

    try:
        photo = message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await telegram_file.download_as_bytearray())

        encoded = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{encoded}"

        answer = await asyncio.to_thread(
            call_openai_image,
            data_url,
            message.caption or "",
        )

        await message.reply_text(answer[:4000])

    except Exception:
        logger.exception("Image question failed")
        await message.reply_text(
            "Image solve karne mein dikkat aayi. "
            "Clear photo bhejkar dobara try karo."
        )


# --------------------------------------------------
# Quiz generation and validation
# --------------------------------------------------

def parse_json_response(raw: str) -> dict:
    raw = raw.strip()

    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    return json.loads(raw)


def validate_questions(data: dict) -> list:
    questions = data.get("questions")

    if not isinstance(questions, list) or not questions:
        raise ValueError("No questions returned")

    validated = []

    for item in questions:
        question_text = str(item.get("question", "")).strip()
        options = item.get("options")
        correct_index = item.get("correct_index")
        explanation = str(item.get("explanation", "")).strip()

        if not question_text:
            raise ValueError("Question text is missing")

        if not isinstance(options, list) or len(options) != 4:
            raise ValueError("Each question must have four options")

        options = [str(option).strip() for option in options]

        if any(not option for option in options):
            raise ValueError("An option is empty")

        if len(set(options)) != 4:
            raise ValueError("Options must be distinct")

        if not isinstance(correct_index, int) or correct_index not in range(4):
            raise ValueError("Invalid correct answer index")

        validated.append(
            {
                "question": question_text,
                "options": options,
                "correct_index": correct_index,
                "explanation": explanation[:200],
            }
        )

    return validated


async def generate_questions_from_text(
    source_text: str,
    topic: Optional[str] = None,
) -> list:
    source_text = source_text[:MAX_DOCUMENT_CHARS]

    if topic:
        source_label = f"Topic: {topic}\n"
        source_instruction = (
            "Create questions using established NEET UG knowledge about this topic."
        )
    else:
        source_label = "Uploaded document text:\n"
        source_instruction = (
            "Use ONLY the facts stated in the uploaded document. "
            "Do not add outside facts."
        )

    prompt = f"""
Create exactly {QUIZ_QUESTION_COUNT} NEET UG multiple-choice questions.

{source_instruction}

Return valid JSON only, in this format:
{{
  "questions": [
    {{
      "question": "Question text",
      "options": ["Option 1", "Option 2", "Option 3", "Option 4"],
      "correct_index": 0,
      "explanation": "Brief explanation"
    }}
  ]
}}

Rules:
- Exactly {QUIZ_QUESTION_COUNT} questions.
- Exactly four distinct options per question.
- Exactly one correct answer per question.
- correct_index must be 0, 1, 2, or 3.
- Avoid duplicate questions.
- Keep the questions appropriate for NEET UG.
- Return JSON only, without Markdown fences.

{source_label}
{source_text}
"""

    raw = await ask_ai(
        prompt,
        "You create accurate, educational NEET UG quizzes and return valid JSON.",
    )

    data = parse_json_response(raw)
    questions = validate_questions(data)

    if len(questions) != QUIZ_QUESTION_COUNT:
        raise ValueError("Incorrect number of questions generated")

    return questions


# --------------------------------------------------
# Interactive Telegram quiz flow
# --------------------------------------------------

async def start_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    questions: list,
    source_name: str,
):
    chat = update.effective_chat
    message = update.effective_message

    context.user_data["active_quiz"] = True
    context.user_data["quiz_questions"] = questions
    context.user_data["quiz_index"] = 0
    context.user_data["quiz_score"] = 0
    context.user_data["quiz_chat_id"] = chat.id
    context.user_data["quiz_source"] = source_name

    await message.reply_text(
        f"✅ {len(questions)} questions ready!\n"
        f"Quiz: {source_name}\n\n"
        "Har question ka answer Telegram poll mein select karo."
    )

    await send_next_question(context, update.effective_user.id)


async def send_next_question(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
):
    data = context.application.user_data.get(user_id, {})
    questions = data.get("quiz_questions", [])
    index = data.get("quiz_index", 0)
    chat_id = data.get("quiz_chat_id")

    if not data.get("active_quiz") or not chat_id:
        return

    if index >= len(questions):
        score = data.get("quiz_score", 0)
        total = len(questions)

        data["active_quiz"] = False

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🏁 Quiz complete!\n\n"
                f"Score: {score}/{total}\n"
                f"Accuracy: {round(score / total * 100) if total else 0}%\n\n"
                "Naya quiz start karne ke liye /quiz <topic> use karo."
            ),
        )
        return

    question = questions[index]

    poll_message = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"Q{index + 1}/{len(questions)}: {question['question']}",
        options=question["options"],
        type="quiz",
        correct_option_id=question["correct_index"],
        explanation=question.get("explanation", "")[:200] or "Answer explanation unavailable.",
        is_anonymous=False,
    )

    data["current_poll_id"] = poll_message.poll.id


async def poll_answer_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    poll_answer = update.poll_answer
    user = poll_answer.user

    if not user:
        return

    data = context.application.user_data.get(user.id, {})

    if not data.get("active_quiz"):
        return

    current_poll_id = data.get("current_poll_id")

    # Ignore answers to old polls.
    if poll_answer.poll_id != current_poll_id:
        return

    questions = data.get("quiz_questions", [])
    index = data.get("quiz_index", 0)

    if index >= len(questions):
        return

    question = questions[index]
    selected = poll_answer.option_ids

    data["total_answered"] = data.get("total_answered", 0) + 1

    if selected and selected[0] == question["correct_index"]:
        data["quiz_score"] = data.get("quiz_score", 0) + 1
        data["total_correct"] = data.get("total_correct", 0) + 1

    data["quiz_index"] = index + 1

    await send_next_question(context, user.id)


# --------------------------------------------------
# Topic quiz command
# --------------------------------------------------

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()

    if not topic:
        await update.effective_message.reply_text(
            "Topic specify karo.\nExample: /quiz Human Physiology"
        )
        return

    if context.user_data.get("active_quiz"):
        await update.effective_message.reply_text(
            "Pehle current quiz complete karo ya /cancel use karo."
        )
        return

    await update.effective_message.reply_text(
        f"🧠 {topic} par quiz bana raha hoon..."
    )

    try:
        questions = await generate_questions_from_text(
            source_text=topic,
            topic=topic,
        )

        await start_quiz(update, context, questions, topic)

    except Exception:
        logger.exception("Topic quiz generation failed")
        await update.effective_message.reply_text(
            "Quiz generate karne mein dikkat aayi. "
            "Topic ko thoda specific karke dobara try karo."
        )


# --------------------------------------------------
# Document text extraction
# --------------------------------------------------

def extract_document_text(filename: str, file_bytes: bytes) -> str:
    filename = filename.lower()

    if filename.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if filename.endswith(".docx"):
        document = Document(io.BytesIO(file_bytes))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)

    if filename.endswith(".txt"):
        return file_bytes.decode("utf-8-sig", errors="replace")

    raise ValueError("Unsupported file type")


async def document_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    document = message.document

    if not document:
        return

    filename = (document.file_name or "").lower()

    if not filename.endswith((".pdf", ".docx", ".txt")):
        await message.reply_text(
            "Sirf PDF, DOCX aur TXT files supported hain."
        )
        return

    if context.user_data.get("active_quiz"):
        await message.reply_text(
            "Pehle current quiz complete karo ya /cancel use karo."
        )
        return

    await message.reply_text(
        "📄 Document download aur read kar raha hoon..."
    )

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        file_bytes = bytes(await telegram_file.download_as_bytearray())

        extracted_text = extract_document_text(filename, file_bytes)

        if len(extracted_text.strip()) < 100:
            await message.reply_text(
                "Document se enough text nahi mila. "
                "Scanned PDF ke liye OCR ki zaroorat ho sakti hai."
            )
            return

        await message.reply_text(
            "🧠 Document se MCQs generate kar raha hoon..."
        )

        questions = await generate_questions_from_text(extracted_text)

        await start_quiz(
            update,
            context,
            questions,
            document.file_name or "Uploaded document",
        )

    except Exception:
        logger.exception("Document quiz failed")
        await message.reply_text(
            "Document quiz banane mein error aaya. "
            "File readable hai aur PDF/DOCX/TXT format mein hai, ye check karo."
        )


# --------------------------------------------------
# Admin command
# --------------------------------------------------

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    if not ADMIN_ID or user_id != ADMIN_ID:
        await update.effective_message.reply_text(
            "Ye command sirf bot admin ke liye hai."
        )
        return

    await update.effective_message.reply_text(
        "Admin access verified.\n"
        "Bot secrets aur settings Render Environment Variables mein manage karo."
    )


# --------------------------------------------------
# Error handling
# --------------------------------------------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error("Telegram update error: %s", context.error)


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable missing")

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY environment variable missing")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("progress", progress_command))
    application.add_handler(CommandHandler("quiz", quiz_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("admin", admin_command))

    application.add_handler(PollAnswerHandler(poll_answer_handler))

    application.add_handler(
        MessageHandler(filters.PHOTO, photo_message)
    )
    application.add_handler(
        MessageHandler(filters.Document.ALL, document_message)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_message)
    )

    application.add_error_handler(error_handler)

    threading.Thread(target=run_web_server, daemon=True).start()

    logger.info("Starting NEET Sathi...")
    application.run_polling()


if __name__ == "__main__":
    main()
