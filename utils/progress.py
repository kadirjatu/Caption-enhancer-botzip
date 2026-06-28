import logging

logger = logging.getLogger(__name__)


def send_progress(bot, chat_id: int, message_id: int, text: str) -> None:
    """
    Edit an existing status message with a new progress text.
    Silently ignores Telegram 'message not modified' errors.
    """
    try:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML")
    except Exception as e:
        err = str(e)
        if "message is not modified" not in err.lower():
            logger.warning(f"Progress update failed: {e}")


def send_new_status(bot, chat_id: int, text: str):
    """Send a fresh status message and return the message object."""
    return bot.send_message(chat_id, text, parse_mode="HTML")
