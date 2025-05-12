import os
import logging
import asyncio
import random
import ssl
import certifi
from typing import Optional, Dict, Any, Union, List, Callable
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import aiohttp
from aiohttp import ClientError, ClientConnectorError, ServerTimeoutError, ClientTimeout

from database import add_favorite, remove_favorite, get_favorites
from config import bot, TMDB_API_KEY, TMDB_BASE_URL, TMDB_IMAGE_BASE_URL

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

last_messages: Dict[int, int] = {}

class MovieStates(StatesGroup):
    waiting_for_search = State()
    waiting_for_genre = State()
    waiting_for_random_criteria = State()

class APIError(Exception):
    pass

async def delete_last_message(chat_id: int):
    if chat_id in last_messages:
        try:
            await bot.delete_message(chat_id, last_messages[chat_id])
        except Exception as e:
            logging.error(f"Error deleting message: {e}")

async def send_message_with_cleanup(chat_id: int, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None, photo: Optional[str] = None):
    await delete_last_message(chat_id)
    try:
        if photo:
            message = await bot.send_photo(chat_id, photo, caption=text, reply_markup=reply_markup)
        else:
            message = await bot.send_message(chat_id, text, reply_markup=reply_markup)
        last_messages[chat_id] = message.message_id
        return message
    except Exception as e:
        logging.error(f"Error sending message: {e}")
        raise

async def make_api_request(session, url, max_retries=3, retry_delay=1):
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    timeout = ClientTimeout(total=30)
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            return await response.json()
                        elif response.status == 429:
                            retry_after = int(response.headers.get('Retry-After', retry_delay))
                            logging.warning(f"Rate limited. Waiting {retry_after} seconds.")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            raise APIError(f"API request failed with status {response.status}")
                except (ClientError, ClientConnectorError, ServerTimeoutError) as e:
                    logging.error(f"API request failed (attempt {attempt + 1}/{max_retries}): {str(e)}")
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logging.info(f"Retrying in {wait_time} seconds...")
                        await asyncio.sleep(wait_time)
                    continue
        except Exception as e:
            logging.error(f"Unexpected error during API request: {str(e)}")
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                logging.info(f"Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            continue
    raise APIError("Max retries exceeded")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск фильма", callback_data="search")],
        [InlineKeyboardButton(text="🎲 Случайный контент", callback_data="random")],
        [InlineKeyboardButton(text="⭐ Избранное", callback_data="favorites")],
        [InlineKeyboardButton(text="📺 Рекомендации", callback_data="recommendations")]
    ])
    await send_message_with_cleanup(
        message.chat.id,
        "Привет! Я помогу тебе найти интересные фильмы и сериалы.\nВыбери действие:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "search")
async def process_search(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message:
        return
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ])
    await send_message_with_cleanup(
        callback.message.chat.id,
        "Введите название фильма для поиска:",
        reply_markup=keyboard
    )
    await state.set_state(MovieStates.waiting_for_search)

@dp.message(MovieStates.waiting_for_search)
async def process_search_query(message: types.Message, state: FSMContext):
    if not message or not message.text:
        return
        
    async with aiohttp.ClientSession() as session:
        url = f"{TMDB_BASE_URL}/search/multi?api_key={TMDB_API_KEY}&language=ru-RU&query={message.text}"
        data = await make_api_request(session, url)
        
        if not data or not isinstance(data, dict) or not data.get("results"):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            await send_message_with_cleanup(
                message.chat.id,
                "По вашему запросу ничего не найдено.",
                reply_markup=keyboard
            )
            return
            
        results = [item for item in data.get("results", []) if item.get("media_type") in ["movie", "tv"]]
        total_pages = len(results)
        
        await state.update_data(search_results=results, current_page=0, total_pages=total_pages, content_type="search")
        await show_search_results(message.chat.id, results[0], "search", 0, total_pages)

async def show_search_results(chat_id: int, item: dict, content_type: str, current_page: int, total_pages: int):
    if not isinstance(item, dict) or not item.get("id"):
        return
        
    text = (
        f"🔍 Результат поиска ({current_page + 1} из {total_pages}):\n\n"
        f"🎬 {item.get('title', item.get('name', 'Нет названия'))}\n"
        f"📅 Год: {item.get('release_date', item.get('first_air_date', 'Неизвестно'))[:4]}\n"
        f"⭐ Рейтинг: {item.get('vote_average', 'Нет рейтинга')}/10\n"
        f"📝 {item.get('overview', 'Нет описания')}\n"
    )

    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data="prev_page"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data="next_page"))
    nav_buttons.append(InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main"))

    action_buttons = [
        [InlineKeyboardButton(text="⭐ Добавить в избранное", callback_data=f"add_favorite_{item.get('id')}")],
        [InlineKeyboardButton(text="📺 Похожие фильмы", callback_data=f"similar_{item.get('id')}")]
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=action_buttons + [nav_buttons])
    
    if item.get("poster_path"):
        await send_message_with_cleanup(
            chat_id,
            text,
            reply_markup=keyboard,
            photo=f"{TMDB_IMAGE_BASE_URL}{item['poster_path']}"
        )
    else:
        await send_message_with_cleanup(chat_id, text, reply_markup=keyboard)

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message:
        return
        
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск фильма", callback_data="search")],
        [InlineKeyboardButton(text="🎲 Случайный контент", callback_data="random")],
        [InlineKeyboardButton(text="⭐ Избранное", callback_data="favorites")],
        [InlineKeyboardButton(text="📺 Рекомендации", callback_data="recommendations")]
    ])
    await send_message_with_cleanup(
        callback.message.chat.id,
        "Выбери действие:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "random")
async def process_random(callback: types.CallbackQuery):
    if not callback.message:
        return
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Фильм", callback_data="random_movie")],
        [InlineKeyboardButton(text="📺 Сериал", callback_data="random_tv")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ])
    await send_message_with_cleanup(
        callback.message.chat.id,
        "Выберите тип контента:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "recommendations")
async def show_recommendations_menu(callback: types.CallbackQuery):
    if not callback.message:
        return
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Популярные фильмы", callback_data="popular_movies")],
        [InlineKeyboardButton(text="📺 Популярные сериалы", callback_data="popular_tv")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ])
    await send_message_with_cleanup(
        callback.message.chat.id,
        "Выберите категорию рекомендаций:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "popular_movies")
async def show_popular_movies(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message:
        return
        
    async with aiohttp.ClientSession() as session:
        url = f"{TMDB_BASE_URL}/movie/popular?api_key={TMDB_API_KEY}&language=ru-RU&page=1"
        data = await make_api_request(session, url)
        
        if not data or not isinstance(data, dict) or not data.get("results"):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            await send_message_with_cleanup(
                callback.message.chat.id,
                "Не удалось получить популярные фильмы. Попробуйте позже.",
                reply_markup=keyboard
            )
            return
            
        results = data.get("results", [])[:10]
        await state.update_data(popular_results=results, current_page=0, total_pages=len(results), content_type="movie")
        await show_popular_content(callback.message.chat.id, results[0], "movie", 0, len(results))

@dp.callback_query(F.data == "popular_tv")
async def show_popular_tv(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message:
        return
        
    async with aiohttp.ClientSession() as session:
        url = f"{TMDB_BASE_URL}/tv/popular?api_key={TMDB_API_KEY}&language=ru-RU&page=1"
        data = await make_api_request(session, url)
        
        if not data or not isinstance(data, dict) or not data.get("results"):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            await send_message_with_cleanup(
                callback.message.chat.id,
                "Не удалось получить популярные сериалы. Попробуйте позже.",
                reply_markup=keyboard
            )
            return
            
        results = data.get("results", [])[:10]
        await state.update_data(popular_results=results, current_page=0, total_pages=len(results), content_type="tv")
        await show_popular_content(callback.message.chat.id, results[0], "tv", 0, len(results))

async def show_popular_content(chat_id: int, item: dict, content_type: str, current_page: int, total_pages: int):
    if not isinstance(item, dict) or not item.get("id"):
        return
        
    text = (
        f"📺 Топ {content_type} ({current_page + 1} из {total_pages}):\n\n"
        f"🎬 {item.get('title', item.get('name', 'Нет названия'))}\n"
        f"📅 Год: {item.get('release_date', item.get('first_air_date', 'Неизвестно'))[:4]}\n"
        f"⭐ Рейтинг: {item.get('vote_average', 'Нет рейтинга')}/10\n"
        f"📝 {item.get('overview', 'Нет описания')}\n"
    )
    
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data="prev_popular"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data="next_popular"))
    nav_buttons.append(InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main"))
    
    action_buttons = [
        [InlineKeyboardButton(text="⭐ Добавить в избранное", callback_data=f"add_favorite_{item.get('id')}")]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=action_buttons + [nav_buttons])
    
    if item.get("poster_path"):
        await send_message_with_cleanup(
            chat_id,
            text,
            reply_markup=keyboard,
            photo=f"{TMDB_IMAGE_BASE_URL}{item['poster_path']}"
        )
    else:
        await send_message_with_cleanup(chat_id, text, reply_markup=keyboard)

@dp.callback_query(F.data == "favorites")
async def show_favorites(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message:
        return
        
    favorites = get_favorites(callback.from_user.id)
    
    if not favorites:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
        ])
        await send_message_with_cleanup(
            callback.message.chat.id,
            "У вас пока нет избранных фильмов.",
            reply_markup=keyboard
        )
        return
    
    favorites_dicts = []
    for fav in favorites:
        favorites_dicts.append({
            'id': fav[2],
            'title': fav[3],
            'poster_path': fav[4],
            'overview': '',
            'vote_average': 0,
            'release_date': ''
        })
    
    await state.update_data(favorites=favorites_dicts, current_page=0, total_pages=len(favorites_dicts))
    await show_favorites_page(callback.message.chat.id, favorites_dicts[0], 0, len(favorites_dicts))

async def show_favorites_page(chat_id: int, item: dict, current_page: int, total_pages: int):
    if not isinstance(item, dict) or not item.get("id"):
        return
        
    text = (
        f"⭐ Избранное ({current_page + 1} из {total_pages}):\n\n"
        f"🎬 {item.get('title', item.get('name', 'Нет названия'))}\n"
        f"📅 Год: {item.get('release_date', item.get('first_air_date', 'Неизвестно'))[:4]}\n"
        f"⭐ Рейтинг: {item.get('vote_average', 'Нет рейтинга')}/10\n"
        f"📝 {item.get('overview', 'Нет описания')}\n"
    )
    
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data="prev_favorite"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data="next_favorite"))
    nav_buttons.append(InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main"))
    
    action_buttons = [
        [InlineKeyboardButton(text="❌ Удалить из избранного", callback_data=f"remove_favorite_{item.get('id')}")]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=action_buttons + [nav_buttons])
    
    if item.get("poster_path"):
        await send_message_with_cleanup(
            chat_id,
            text,
            reply_markup=keyboard,
            photo=f"{TMDB_IMAGE_BASE_URL}{item['poster_path']}"
        )
    else:
        await send_message_with_cleanup(chat_id, text, reply_markup=keyboard)

async def handle_navigation(callback: types.CallbackQuery, state: FSMContext, 
                          data_key: str, show_func: Callable, 
                          prev_callback: str, next_callback: str):
    if not callback.message:
        return
        
    data = await state.get_data()
    current_page = data.get("current_page", 0)
    results = data.get(data_key, [])
    content_type = data.get("content_type", "movie")
    
    if callback.data == prev_callback and current_page > 0:
        current_page -= 1
        await state.update_data(current_page=current_page)
        await show_func(callback.message.chat.id, results[current_page], content_type, current_page, len(results))
    elif callback.data == next_callback and current_page < len(results) - 1:
        current_page += 1
        await state.update_data(current_page=current_page)
        await show_func(callback.message.chat.id, results[current_page], content_type, current_page, len(results))

@dp.callback_query(F.data == "prev_page")
async def prev_page(callback: types.CallbackQuery, state: FSMContext):
    await handle_navigation(callback, state, "search_results", show_search_results, "prev_page", "next_page")

@dp.callback_query(F.data == "next_page")
async def next_page(callback: types.CallbackQuery, state: FSMContext):
    await handle_navigation(callback, state, "search_results", show_search_results, "prev_page", "next_page")

@dp.callback_query(F.data == "prev_popular")
async def prev_popular(callback: types.CallbackQuery, state: FSMContext):
    await handle_navigation(callback, state, "popular_results", show_popular_content, "prev_popular", "next_popular")

@dp.callback_query(F.data == "next_popular")
async def next_popular(callback: types.CallbackQuery, state: FSMContext):
    await handle_navigation(callback, state, "popular_results", show_popular_content, "prev_popular", "next_popular")

@dp.callback_query(F.data == "prev_similar")
async def prev_similar(callback: types.CallbackQuery, state: FSMContext):
    await handle_navigation(callback, state, "similar_results", show_similar_content, "prev_similar", "next_similar")

@dp.callback_query(F.data == "next_similar")
async def next_similar(callback: types.CallbackQuery, state: FSMContext):
    await handle_navigation(callback, state, "similar_results", show_similar_content, "prev_similar", "next_similar")

@dp.callback_query(F.data == "prev_favorite")
async def prev_favorite(callback: types.CallbackQuery, state: FSMContext):
    await handle_navigation(callback, state, "favorites", show_favorites_page, "prev_favorite", "next_favorite")

@dp.callback_query(F.data == "next_favorite")
async def next_favorite(callback: types.CallbackQuery, state: FSMContext):
    await handle_navigation(callback, state, "favorites", show_favorites_page, "prev_favorite", "next_favorite")

@dp.callback_query(F.data.startswith("remove_favorite_"))
async def remove_from_favorites(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message or not callback.data:
        return
        
    try:
        movie_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return
        
    if remove_favorite(callback.from_user.id, movie_id):
        await callback.answer("Фильм удален из избранного!")
        
        data = await state.get_data()
        favorites = data.get("favorites", [])
        current_page = data.get("current_page", 0)
        
        favorites = [f for f in favorites if f['id'] != movie_id]
        
        if not favorites:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            await send_message_with_cleanup(
                callback.message.chat.id,
                "У вас пока нет избранных фильмов.",
                reply_markup=keyboard
            )
            return
            
        if current_page >= len(favorites):
            current_page = len(favorites) - 1
            
        await state.update_data(favorites=favorites, current_page=current_page)
        await show_favorites_page(callback.message.chat.id, favorites[current_page], current_page, len(favorites))
    else:
        await callback.answer("Ошибка при удалении из избранного")

@dp.message()
async def handle_unknown_message(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск фильма", callback_data="search")],
        [InlineKeyboardButton(text="🎲 Случайный контент", callback_data="random")],
        [InlineKeyboardButton(text="⭐ Избранное", callback_data="favorites")],
        [InlineKeyboardButton(text="📺 Рекомендации", callback_data="recommendations")]
    ])
    
    await send_message_with_cleanup(
        message.chat.id,
        "Для начала работы с ботом используйте команду /start\nИли выберите действие из меню ниже:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "random_movie")
async def show_random_movie(callback: types.CallbackQuery):
    if not callback.message:
        return
        
    async with aiohttp.ClientSession() as session:
        page = random.randint(1, 500)
        url = f"{TMDB_BASE_URL}/movie/popular?api_key={TMDB_API_KEY}&language=ru-RU&page={page}"
        data = await make_api_request(session, url)
        
        if not data or not isinstance(data, dict) or not data.get("results"):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="random")]
            ])
            await send_message_with_cleanup(
                callback.message.chat.id,
                "Не удалось получить случайный фильм. Попробуйте еще раз.",
                reply_markup=keyboard
            )
            return
            
        movie = random.choice(data["results"])
        
        text = (
            f"🎲 Случайный фильм:\n\n"
            f"🎬 {movie.get('title', 'Нет названия')}\n"
            f"📅 Год: {movie.get('release_date', 'Неизвестно')[:4]}\n"
            f"⭐ Рейтинг: {movie.get('vote_average', 'Нет рейтинга')}/10\n"
            f"📝 {movie.get('overview', 'Нет описания')}\n"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Добавить в избранное", callback_data=f"add_favorite_{movie.get('id')}")],
            [InlineKeyboardButton(text="🎲 Другой фильм", callback_data="random_movie")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="random")]
        ])
        
        if movie.get("poster_path"):
            await send_message_with_cleanup(
                callback.message.chat.id,
                text,
                reply_markup=keyboard,
                photo=f"{TMDB_IMAGE_BASE_URL}{movie['poster_path']}"
            )
        else:
            await send_message_with_cleanup(callback.message.chat.id, text, reply_markup=keyboard)

@dp.callback_query(F.data == "random_tv")
async def show_random_tv(callback: types.CallbackQuery):
    if not callback.message:
        return
        
    async with aiohttp.ClientSession() as session:
        page = random.randint(1, 500)
        url = f"{TMDB_BASE_URL}/tv/popular?api_key={TMDB_API_KEY}&language=ru-RU&page={page}"
        data = await make_api_request(session, url)
        
        if not data or not isinstance(data, dict) or not data.get("results"):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="random")]
            ])
            await send_message_with_cleanup(
                callback.message.chat.id,
                "Не удалось получить случайный сериал. Попробуйте еще раз.",
                reply_markup=keyboard
            )
            return
            
        tv_show = random.choice(data["results"])
        
        text = (
            f"🎲 Случайный сериал:\n\n"
            f"📺 {tv_show.get('name', 'Нет названия')}\n"
            f"📅 Год: {tv_show.get('first_air_date', 'Неизвестно')[:4]}\n"
            f"⭐ Рейтинг: {tv_show.get('vote_average', 'Нет рейтинга')}/10\n"
            f"📝 {tv_show.get('overview', 'Нет описания')}\n"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Добавить в избранное", callback_data=f"add_favorite_{tv_show.get('id')}")],
            [InlineKeyboardButton(text="🎲 Другой сериал", callback_data="random_tv")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="random")]
        ])
        
        if tv_show.get("poster_path"):
            await send_message_with_cleanup(
                callback.message.chat.id,
                text,
                reply_markup=keyboard,
                photo=f"{TMDB_IMAGE_BASE_URL}{tv_show['poster_path']}"
            )
        else:
            await send_message_with_cleanup(callback.message.chat.id, text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("similar_"))
async def show_similar_movies(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message or not callback.data:
        return
        
    try:
        movie_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return
        
    async with aiohttp.ClientSession() as session:
        url = f"{TMDB_BASE_URL}/movie/{movie_id}/similar?api_key={TMDB_API_KEY}&language=ru-RU&page=1"
        data = await make_api_request(session, url)
        
        if not data or not isinstance(data, dict) or not data.get("results"):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
            await send_message_with_cleanup(
                callback.message.chat.id,
                "Не удалось найти похожие фильмы. Попробуйте позже.",
                reply_markup=keyboard
            )
            return
            
        results = data.get("results", [])[:10]
        await state.update_data(similar_results=results, current_page=0, total_pages=len(results))
        await show_similar_content(callback.message.chat.id, results[0], 0, len(results))

async def show_similar_content(chat_id: int, item: dict, current_page: int, total_pages: int):
    if not isinstance(item, dict) or not item.get("id"):
        return
        
    text = (
        f"🎬 Похожие фильмы ({current_page + 1} из {total_pages}):\n\n"
        f"🎬 {item.get('title', 'Нет названия')}\n"
        f"📅 Год: {item.get('release_date', 'Неизвестно')[:4]}\n"
        f"⭐ Рейтинг: {item.get('vote_average', 'Нет рейтинга')}/10\n"
        f"📝 {item.get('overview', 'Нет описания')}\n"
    )
    
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data="prev_similar"))
    if current_page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data="next_similar"))
    nav_buttons.append(InlineKeyboardButton(text="◀️ В главное меню", callback_data="back_to_main"))
    
    action_buttons = [
        [InlineKeyboardButton(text="⭐ Добавить в избранное", callback_data=f"add_favorite_{item.get('id')}")]
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=action_buttons + [nav_buttons])
    
    if item.get("poster_path"):
        await send_message_with_cleanup(
            chat_id,
            text,
            reply_markup=keyboard,
            photo=f"{TMDB_IMAGE_BASE_URL}{item['poster_path']}"
        )
    else:
        await send_message_with_cleanup(chat_id, text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("add_favorite_"))
async def add_to_favorites(callback: types.CallbackQuery):
    if not callback.message or not callback.data:
        return
        
    try:
        movie_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return
        
    async with aiohttp.ClientSession() as session:
        url = f"{TMDB_BASE_URL}/movie/{movie_id}?api_key={TMDB_API_KEY}&language=ru-RU"
        data = await make_api_request(session, url)
        
        if not data or not isinstance(data, dict):
            await callback.answer("❌ Ошибка при добавлении в избранное")
            return
            
        title = data.get("title", "Неизвестный фильм")
        poster_path = data.get("poster_path", "")
        
        if add_favorite(callback.from_user.id, movie_id, title, poster_path):
            await callback.answer("✅ Фильм добавлен в избранное!")
        else:
            await callback.answer("❌ Фильм уже в избранном")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()) 