import logging
from aiogram import Bot
from typing import Optional

BOT_TOKEN = "8080166792:AAHGb-p7tIns6qFG8pRBDcZq4gP56MI_OS4"
TMDB_API_KEY = "e56533e6144245a6bd2bccbc5da62329"

bot = Bot(token=BOT_TOKEN)

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
) 