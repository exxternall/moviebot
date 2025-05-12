import sqlite3
import logging
from typing import List, Optional, Tuple

def get_db_connection():
    try:
        conn = sqlite3.connect('movie_bot.db')
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logging.error(f"Error connecting to database: {e}")
        raise

def init_db():
    conn = get_db_connection()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                poster_path TEXT
            )
        ''')
        conn.commit()
    finally:
        conn.close()

def add_favorite(user_id: int, movie_id: int, title: str, poster_path: str) -> bool:
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO favorites (user_id, movie_id, title, poster_path) VALUES (?, ?, ?, ?)",
            (user_id, movie_id, title, poster_path)
        )
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error adding favorite: {e}")
        return False
    finally:
        conn.close()

def remove_favorite(user_id: int, movie_id: int) -> bool:
    try:
        conn = get_db_connection()
        conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND movie_id = ?",
            (user_id, movie_id)
        )
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error removing favorite: {e}")
        return False
    finally:
        conn.close()

def get_favorites(user_id: int) -> List[Tuple]:
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            "SELECT * FROM favorites WHERE user_id = ?",
            (user_id,)
        )
        return cursor.fetchall()
    except Exception as e:
        logging.error(f"Error getting favorites: {e}")
        return []
    finally:
        conn.close()

init_db() 