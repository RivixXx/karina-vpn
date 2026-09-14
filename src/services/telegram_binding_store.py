import time
from contextlib import closing

from .errors import ReconciliationRequiredError


class TelegramBindingStore:
    def __init__(self, connect, *, now_provider=None):
        self._connect = connect
        self._now = now_provider or (lambda: int(time.time()))

    def get(self, tg_id):
        with closing(self._connect()) as db:
            return db.execute("SELECT * FROM telegram_links WHERE tg_id=?", (tg_id,)).fetchone()

    def create(self, tg_id, email):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT tg_id, email FROM telegram_links WHERE tg_id=? OR email=?",
                              (tg_id, email)).fetchall()
            if rows:
                if len(rows) == 1 and rows[0][0] == tg_id and rows[0][1] == email:
                    return
                raise ReconciliationRequiredError("Payment binding conflicts with existing ownership")
            db.execute("INSERT INTO telegram_links(tg_id, email, linked_at) VALUES (?, ?, ?)",
                       (tg_id, email, self._now()))
