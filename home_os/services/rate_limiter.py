import sqlite3
import time
import threading


class RateLimiter:
    """SQLite-backed rate limiter that shares state across Gunicorn workers.

    Uses a dedicated SQLite database (not the main app DB) so it works
    without Flask app context and persists across service restarts.
    """

    _cleanup_interval = 60  # seconds between cleanup runs

    def __init__(self, db_path="/tmp/home_os_rate_limit.db",
                 max_attempts=5, window_seconds=900, per_account_max=10):
        self.db_path = db_path
        self.max_attempts = max_attempts
        self.window = window_seconds
        self.per_account_max = per_account_max
        self._last_cleanup = 0
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self):
        """Get a new connection with WAL mode for concurrent access."""
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        return conn

    def _init_db(self):
        """Create the attempts table if it doesn't exist."""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limit_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rate_limit_key_ts
                ON rate_limit_attempts (key, timestamp)
            """)
            conn.commit()
        finally:
            conn.close()

    def _maybe_cleanup(self, conn):
        """Remove expired entries periodically to prevent DB growth."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        with self._lock:
            if now - self._last_cleanup < self._cleanup_interval:
                return
            self._last_cleanup = now
            cutoff = now - self.window
            conn.execute(
                "DELETE FROM rate_limit_attempts WHERE timestamp < ?",
                (cutoff,)
            )
            conn.commit()

    def is_limited(self, key, max_attempts=None):
        """Check if a key has exceeded its attempt limit within the window.

        Args:
            key: The identifier to check (IP address or username).
            max_attempts: Override the default max_attempts threshold.
                          Use per_account_max for username keys.
        """
        if max_attempts is None:
            max_attempts = self.max_attempts

        now = time.time()
        cutoff = now - self.window
        conn = self._get_conn()
        try:
            self._maybe_cleanup(conn)
            row = conn.execute(
                "SELECT COUNT(*) FROM rate_limit_attempts "
                "WHERE key = ? AND timestamp >= ?",
                (key, cutoff)
            ).fetchone()
            return row[0] >= max_attempts
        finally:
            conn.close()

    def record(self, key):
        """Record a failed attempt for the given key."""
        now = time.time()
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO rate_limit_attempts (key, timestamp) VALUES (?, ?)",
                (key, now)
            )
            conn.commit()
        finally:
            conn.close()

    def reset(self, key):
        """Clear all attempts for the given key (e.g., on successful login)."""
        conn = self._get_conn()
        try:
            conn.execute(
                "DELETE FROM rate_limit_attempts WHERE key = ?",
                (key,)
            )
            conn.commit()
        finally:
            conn.close()


login_limiter = RateLimiter(
    db_path="/tmp/home_os_rate_limit.db",
    max_attempts=5,
    window_seconds=900,
    per_account_max=10,
)
