import os
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
ADMIN_USERNAME = "matvei_dev"
PORT = int(os.getenv("PORT", 8000))

CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_URL = os.getenv("CHANNEL_URL")
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID")
CHANNEL_2_URL = os.getenv("CHANNEL_2_URL")

PANEL_URL = os.getenv("PANEL_URL", "")
PANEL_USERNAME = os.getenv("PANEL_USERNAME", "")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
INBOUND_ID = int(os.getenv("INBOUND_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

SERVER_IP = os.getenv("SERVER_IP")
SERVER_PORT = os.getenv("SERVER_PORT")
REALITY_PK = os.getenv("REALITY_PK")
SNI = os.getenv("SNI")
SID = os.getenv("SID", "")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())