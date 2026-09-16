"""
🌉 RAGEBITE USERBOT BRIDGE
"""

from telethon import TelegramClient, events

API_ID = 30850814
API_HASH = '411f782a2b5bc2e5c562d7921480072a'

SOURCE_BOT = '@test_swarg_bot'
TARGET_BOT = '@maIN_SWARGBOT'

SESSION_NAME = 'ragebite_bridge_session'

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)


@client.on(events.NewMessage(chats=SOURCE_BOT))
async def handler(event):
    text = (event.raw_text or "").strip()

    if not text.startswith("/bgmi"):
        return

    print(f"📩 Command: {text}")
    try:
        await client.send_message(TARGET_BOT, text)
        print(f"🚀 Forwarded to {TARGET_BOT}")
    except Exception as e:
        print(f"❌ Forward failed: {e}")


def main():
    print("=" * 60)
    print("🌉 RAGEBITE USERBOT BRIDGE")
    print("=" * 60)
    print(f"📥 Source: {SOURCE_BOT}")
    print(f"📤 Target: {TARGET_BOT}")
    print("=" * 60)
    print("⚡ Bridge running...")
    print("=" * 60)

    client.start()
    client.run_until_disconnected()


if __name__ == '__main__':
    main()
