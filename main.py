#!/usr/bin/env python3
import os
import re
import sys
import json
import logging
import asyncio
import shutil
import subprocess
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# Load environment variables from .env file if available
load_dotenv()

# ==========================================
# CONFIGURATION & SECURITY CONSTANTS
# ==========================================
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "123456789"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")

# State file to persist conversation context across bot restarts
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# Ensure typical paths are present in system PATH for subprocess execution
LOCAL_BIN = os.path.expanduser("~/.local/bin")
GEMINI_BIN = os.path.expanduser("~/.gemini/antigravity-cli/bin")
path_env = os.environ.get("PATH", "")
paths = path_env.split(os.pathsep)

for extra_path in (LOCAL_BIN, GEMINI_BIN):
    if extra_path and extra_path not in paths:
        paths.insert(0, extra_path)
os.environ["PATH"] = os.pathsep.join(paths)

# ==========================================
# LOGGING SETUP
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("AntigravityBot")

# ==========================================
# CONCURRENCY CONTROL (Locks per user)
# ==========================================
user_locks = {}

def get_lock_for_user(user_id: int) -> asyncio.Lock:
    """Returns a thread/coroutine lock for the specific user to serialize execution."""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

# ==========================================
# STATE PERSISTENCE HELPERS
# ==========================================
def load_state() -> dict:
    """Loads conversation mappings and cumulative histories from disk."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading state.json: {e}")
    return {"conversations": {}}

def save_state(state: dict) -> None:
    """Saves the conversation mapping state to disk."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving state.json: {e}")

def extract_conversation_id_from_log(log_path: str) -> str | None:
    """
    Parses the agy execution log file to extract the conversation ID.
    This avoids race conditions and namespace collisions with other sessions.
    """
    if not os.path.exists(log_path):
        return None
    try:
        with open(log_path, "r") as f:
            log_content = f.read()
        
        # Match pattern: "Created conversation <UUID>" or "found conversation <UUID>"
        matches = re.findall(r'(?:Created|found) conversation ([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', log_content)
        if matches:
            return matches[-1]
            
        # Fallback to match "conversation=<UUID>"
        matches = re.findall(r'conversation=([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', log_content)
        if matches:
            return matches[-1]
    except Exception as e:
        logger.error(f"Error parsing log file {log_path}: {e}")
    return None

# ==========================================
# COMMAND HANDLERS & HELPERS
# ==========================================
def chunk_text(text: str, max_len: int = 4096) -> list[str]:
    """
    Intelligently splits the text into chunks of maximum max_len.
    Tries to split at double newlines or single newlines to preserve readability.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        split_point = -1
        for delimiter in ("\n\n", "\n", " "):
            pos = text.rfind(delimiter, 0, max_len)
            if pos != -1 and pos > (max_len - 400):
                split_point = pos + len(delimiter)
                break

        if split_point == -1:
            split_point = max_len

        chunks.append(text[:split_point])
        text = text[split_point:]

    return chunks

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets the user and verifies authorization status."""
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None

    if user_id != ALLOWED_USER_ID and chat_id != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized /start attempt from user_id: {user_id}, chat_id: {chat_id}")
        return

    welcome_message = (
        "🤖 **Antigravity CLI Bot Bridge**\n\n"
        "Status: Authorized ✅\n"
        "Send me a message/prompt, and I will execute it locally on this machine using "
        "the `agy` tool and return the output to you.\n\n"
        "**Available Commands:**\n"
        "• `/reset` - Clear the current conversation context and start a new session."
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resets the conversation mapping for the authorized user."""
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None

    if user_id != ALLOWED_USER_ID and chat_id != ALLOWED_USER_ID:
        return

    state = load_state()
    user_str = str(user_id)
    
    if user_str in state["conversations"]:
        del state["conversations"][user_str]
        save_state(state)
        
        # Also clean up the user's log file to prevent stale UUID extraction
        log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"last_run_{user_id}.log")
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except Exception as e:
                logger.warning(f"Could not remove log file on reset: {e}")
                
        logger.info(f"Context reset for user: {user_id}")
        await update.message.reply_text("🔄 Conversation context reset successfully. Your next message will start a fresh session.")
    else:
        await update.message.reply_text("No active conversation context to reset.")


async def send_typing_indicator_loop(bot, chat_id: int, stop_event: asyncio.Event) -> None:
    """Periodically sends typing indicator to Telegram chat every 4 seconds."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception as e:
            logger.error(f"Error sending typing action: {e}")
        
        # Wait 4 seconds or until the event is set
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes incoming messages, executes agy command maintaining context, and returns the response."""
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None

    # CRITICAL SECURITY STEP: Inspect user_id and chat_id. Ignore completely if unauthorized.
    if user_id != ALLOWED_USER_ID and chat_id != ALLOWED_USER_ID:
        logger.warning(f"Drop unauthorized message from user_id: {user_id}, chat_id: {chat_id}")
        return

    if not update.message:
        return

    image_path = None
    prompt_text = ""

    if update.message.text:
        prompt_text = update.message.text
    elif update.message.photo:
        try:
            photo_file = await update.message.photo[-1].get_file()
            image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_image.jpg")
            await photo_file.download_to_drive(image_path)
            caption = update.message.caption or ""
            if caption:
                prompt_text = f"Image attached: telegram_image.jpg\n\nUser prompt: {caption}"
            else:
                prompt_text = "Image attached: telegram_image.jpg\n\nPlease analyze this image."
        except Exception as e:
            logger.error(f"Error downloading photo: {e}")
            await update.message.reply_text("⚠️ Failed to download the attached photo.")
            return
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("image/"):
        try:
            doc_file = await update.message.document.get_file()
            ext = os.path.splitext(update.message.document.file_name)[1] or ".jpg"
            image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"telegram_image{ext}")
            await doc_file.download_to_drive(image_path)
            filename = f"telegram_image{ext}"
            caption = update.message.caption or ""
            if caption:
                prompt_text = f"Image attached: {filename}\n\nUser prompt: {caption}"
            else:
                prompt_text = f"Image attached: {filename}\n\nPlease analyze this image."
        except Exception as e:
            logger.error(f"Error downloading document image: {e}")
            await update.message.reply_text("⚠️ Failed to download the attached image file.")
            return
    else:
        return

    logger.info(f"Received prompt from user: {user_id}")

    # Concurrency check: check if lock is already held
    lock = get_lock_for_user(user_id)
    if lock.locked():
        await update.message.reply_text("⏳ I am currently processing your previous request. Please wait...")

    # Wait for execution turn
    async with lock:
        # Load context state
        state = load_state()
        user_str = str(user_id)
        user_conv = state["conversations"].get(user_str, {})
        conv_id = user_conv.get("conversation_id")
        prev_resp = user_conv.get("previous_cumulative_response", "")

        # Override log file path for this run to avoid race conditions
        log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"last_run_{user_id}.log")

        # Delete previous run's log file if it exists to ensure we only parse the current run's log
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except Exception as e:
                logger.warning(f"Could not remove old log file {log_file}: {e}")

        # Build agy command execution arguments (including dangerously-skip-permissions to avoid prompts blocking)
        if conv_id:
            command = ["agy", "--log-file", log_file, "--conversation", conv_id, "--dangerously-skip-permissions", "--print", prompt_text]
            logger.info(f"Continuing conversation UUID: {conv_id}")
        else:
            command = ["agy", "--log-file", log_file, "--dangerously-skip-permissions", "--print", prompt_text]
            logger.info("Starting a new conversation session")

        # Start periodic typing status in the background
        stop_typing_event = asyncio.Event()
        typing_task = asyncio.create_task(
            send_typing_indicator_loop(context.bot, update.effective_chat.id, stop_typing_event)
        )

        try:
            # Clean environment variables to prevent agy from inheriting parent session context
            clean_env = os.environ.copy()
            for key in list(clean_env.keys()):
                if key.startswith("ANTIGRAVITY_") or key.startswith("GEMINI_") or "CONVERSATION" in key:
                    del clean_env[key]

            # Run local CLI tool asynchronously in a thread pool to avoid blocking the event loop
            def run_agy():
                return subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=clean_env
                )

            result = await asyncio.to_thread(run_agy)
            
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            
            raw_output = stdout
            if result.returncode != 0:
                logger.error(f"agy CLI returned non-zero code: {result.returncode}")
                if stderr:
                    raw_output += f"\n\n[CLI error trace]\n{stderr}"
                elif not stdout:
                    raw_output = f"CLI failure. Return code: {result.returncode}"
                    
            if not raw_output.strip() and stderr:
                raw_output = stderr

        except subprocess.TimeoutExpired as e:
            logger.error(f"Process timeout expired: {e}")
            await update.message.reply_text("⚠️ Timeout: The Antigravity CLI took too long to respond.")
            return
        except FileNotFoundError:
            logger.error("The 'agy' CLI executable could not be found.")
            await update.message.reply_text("⚠️ Error: The local executable `agy` could not be found in PATH.")
            return
        except Exception as e:
            logger.error(f"Unexpected execution failure: {e}")
            await update.message.reply_text(f"⚠️ Execution Error: {str(e)}")
            return
        finally:
            # Stop the typing loop
            stop_typing_event.set()
            await typing_task

        # Clean raw output by removing thinking blocks
        sanitized_output = re.sub(r"<think>.*?</think>", "", raw_output, flags=re.DOTALL).strip()

        # Subtract the previous cumulative response to output only the new turn's output
        clean_prev = prev_resp.strip()
        if clean_prev and sanitized_output.startswith(clean_prev):
            final_output = sanitized_output[len(clean_prev):].strip()
        else:
            final_output = sanitized_output

        if not final_output:
            final_output = "(CLI command finished with no content output)"

        # Get and update the conversation ID
        new_conv_id = extract_conversation_id_from_log(log_file)
        if new_conv_id:
            state["conversations"][user_str] = {
                "conversation_id": new_conv_id,
                "previous_cumulative_response": sanitized_output
            }
            save_state(state)
            logger.info(f"Persisted conversation state for user {user_str} under UUID {new_conv_id}")

        # Deliver response chunks
        response_chunks = chunk_text(final_output)
        for chunk in response_chunks:
            try:
                await update.message.reply_text(chunk)
            except Exception as e:
                logger.error(f"Failed to deliver message chunk: {e}")

# ==========================================
# MAIN ENTRYPOINT
# ==========================================
def main() -> None:
    logger.info("Initializing Antigravity Telegram Bot Bridge...")
    
    if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE" or not TELEGRAM_TOKEN:
        logger.critical("TELEGRAM_TOKEN is not configured! Exiting.")
        sys.exit("Error: Please set TELEGRAM_TOKEN in the environment or .env file.")

    # Build python-telegram-bot application
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", start_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, handle_message))

    # Start long polling
    logger.info(f"Polling started. Access restricted to User ID: {ALLOWED_USER_ID}")
    application.run_polling()

if __name__ == "__main__":
    main()
