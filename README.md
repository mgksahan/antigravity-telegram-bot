# Antigravity Telegram Bot Bridge 🤖

A secure, private Telegram Bot bridge that allows you to interface with the local `agy` CLI tool directly from Telegram. It executes commands securely and returns outputs in real-time, maintaining conversation context and handling pagination seamlessly.

## Key Features
- **Strict Access Control**: Only messages from the configured `ALLOWED_USER_ID` are processed; all other inputs are ignored.
- **Context Preservation**: Persists and routes conversation UUIDs automatically back into `agy` to sustain context.
- **Typing Indicator**: Provides responsive feedback in Telegram while the CLI runs.
- **Safety Precaution**: Avoids permission prompts by automatically applying the safety skip flag.

---

## 🛠️ Step-by-Step Setup Guide

### 1. Create a Telegram Bot (via BotFather)
Before starting, you need a Telegram Bot token:
1. Open Telegram and search for the verified account [**@BotFather**](https://t.me/BotFather).
2. Start a chat and send the command `/newbot`.
3. Follow the prompts:
   - Provide a display name for your bot.
   - Choose a unique username ending in `bot` (e.g., `MyAntigravityBridgeBot`).
4. **BotFather** will generate an **HTTP API Token** (formatted as `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`). Copy this token securely.

### 2. Find Your Telegram User ID
For security, the bot only listens to your specific account. To get your numeric user ID:
1. Message [**@userinfobot**](https://t.me/userinfobot) or [**@MissRose_bot**](https://t.me/MissRose_bot) on Telegram.
2. Send `/start` or `/id`.
3. Copy your numeric ID (e.g., `8814560445`).

### 3. Configure the Environment
1. Copy the template configuration file:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and fill in your details:
   ```env
   # Your Telegram Bot Token obtained from BotFather
   TELEGRAM_TOKEN=your_copied_bot_token_here

   # Your actual numeric Telegram User ID
   ALLOWED_USER_ID=your_telegram_user_id_here
   ```

> [!WARNING]
> Keep your `.env` file private and never commit it to version control. It is ignored by default in `.gitignore`.

### 4. Installation & Running

1. **Create and Activate a Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the Bot:**
   ```bash
   python3 main.py
   ```

---

## 🖥️ Running as a Systemd Service

To keep the bot running in the background and survive system restarts, you can configure it as a systemd user or system service. A template is provided in `antigravity-bot.service`.

### Installation Steps (System-wide):
1. Copy the service file to the systemd directory:
   ```bash
   sudo cp antigravity-bot.service /etc/systemd/system/antigravity-bot.service
   ```
2. Reload systemd daemon:
   ```bash
   sudo systemctl daemon-reload
   ```
3. Enable and start the bot service:
   ```bash
   sudo systemctl enable antigravity-bot.service
   sudo systemctl start antigravity-bot.service
   ```
4. Check service status:
   ```bash
   sudo systemctl status antigravity-bot.service
   ```
