import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

# Create a hidden database file safely in your home directory
DB_PATH = Path.home() / ".anime_assistant" / "brain_memory.db"

_init_lock = threading.Lock()
_initialized = False


@contextmanager
def _db():
    """Opens the database, commits, and ALWAYS closes it (sqlite3's own 'with' never closes the connection)."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Ensures the database and required tables exist (only does the work once per run)."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _db() as conn:
            cursor = conn.cursor()

            # Table for rolling conversation history
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT,
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Table for long-term permanent facts
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact TEXT
                )
            ''')
        _initialized = True


def save_message(role: str, content: str):
    """Saves a single user or assistant message. A database problem is reported but never stops a reply."""
    try:
        init_db()
        with _db() as conn:
            conn.execute('INSERT INTO messages (role, content) VALUES (?, ?)', (role, str(content or "")))
    except Exception as e:
        print(f"⚠️ Could not save the message to memory: {e}")


def get_recent_history(limit: int = 6) -> list:
    """Fetches the last N messages to provide short-term context to the LLM."""
    try:
        init_db()
        with _db() as conn:
            # Grab the newest messages first
            rows = conn.execute('SELECT role, content FROM messages ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        # Reverse them so they are in chronological order for the LLM
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]
    except Exception as e:
        print(f"⚠️ Could not read the chat history: {e}")
        return []


def get_all_facts() -> str:
    """Retrieves long-term facts to inject into the system prompt."""
    try:
        init_db()
        with _db() as conn:
            rows = conn.execute('SELECT fact FROM facts').fetchall()
    except Exception as e:
        print(f"⚠️ Could not read the saved facts: {e}")
        rows = []

    if not rows:
        return "No permanent facts established yet."

    facts_formatted = "\n".join([f"- {row[0]}" for row in rows])
    return f"User Facts and Preferences:\n{facts_formatted}"


if __name__ == "__main__":
    # Quick test to ensure the database initializes correctly
    print("Initializing memory database...")
    init_db()
    print(f"✅ Memory DB ready at: {DB_PATH}")
