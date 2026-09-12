import secrets
import sqlite3
from contextlib import closing
from .operation_lock import operation_lock


class ReferralRepository:
    def operation_lock(self):
        return operation_lock(self._connect)

    def __init__(self, db_path, *, connect=None, now_provider=None):
        self.db_path = db_path
        self._connect_factory = connect
        self.now_provider = now_provider or __import__("time").time

    def _connect(self):
        db = self._connect_factory() if self._connect_factory else sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def init_schema(self):
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS referral_profiles (
                    tg_id INTEGER PRIMARY KEY,
                    referral_code TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_tg_id INTEGER NOT NULL,
                    referred_tg_id INTEGER NOT NULL UNIQUE,
                    referral_code TEXT NOT NULL,
                    referred_username TEXT NOT NULL DEFAULT '',
                    referred_first_name TEXT NOT NULL DEFAULT '',
                    attributed_at INTEGER NOT NULL,
                    qualified_at INTEGER,
                    qualifying_order_id TEXT,
                    status TEXT NOT NULL DEFAULT 'attributed'
                        CHECK(status IN ('attributed','qualified','rewarded','rejected')),
                    FOREIGN KEY(referrer_tg_id) REFERENCES referral_profiles(tg_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_referrals_order
                    ON referrals(qualifying_order_id) WHERE qualifying_order_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_referrals_referrer
                    ON referrals(referrer_tg_id);
                CREATE TABLE IF NOT EXISTS referral_rewards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referral_id INTEGER NOT NULL,
                    recipient_tg_id INTEGER NOT NULL,
                    reward_type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    target_expiry_ms INTEGER,
                    created_at INTEGER NOT NULL,
                    applied_at INTEGER,
                    notified_at INTEGER,
                    UNIQUE(referral_id, reward_type),
                    FOREIGN KEY(referral_id) REFERENCES referrals(id)
                );
                CREATE INDEX IF NOT EXISTS idx_rewards_recipient
                    ON referral_rewards(recipient_tg_id);
            """)

    def first_completed_order(self, tg_id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT order_id FROM orders WHERE tg_id=? AND status=\'completed\' "
                             "ORDER BY applied_at, id LIMIT 1", (tg_id,)).fetchone()
            return row[0] if row else None

    def payment_in_progress(self, tg_id):
        with closing(self._connect()) as db:
            return db.execute("SELECT 1 FROM orders WHERE tg_id=? AND status=\'paid\'", (tg_id,)).fetchone() is not None

    def get_or_create_profile(self, tg_id):
        now = int(self.now_provider())
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT * FROM referral_profiles WHERE tg_id = ?", (tg_id,)).fetchone()
            while row is None:
                code = secrets.token_urlsafe(6)
                try:
                    db.execute("INSERT INTO referral_profiles VALUES (?, ?, ?)", (tg_id, code, now))
                except sqlite3.IntegrityError:
                    row = db.execute(
                        "SELECT * FROM referral_profiles WHERE tg_id = ?", (tg_id,),
                    ).fetchone()
                    continue
                row = db.execute("SELECT * FROM referral_profiles WHERE tg_id = ?", (tg_id,)).fetchone()
        return row

    def attribute(self, code, referred_tg_id, username="", first_name=""):
        now = int(self.now_provider())
        with closing(self._connect()) as db, db:
            profile = db.execute(
                "SELECT * FROM referral_profiles WHERE referral_code = ?", (code,),
            ).fetchone()
            if profile is None or profile["tg_id"] == referred_tg_id:
                return None
            db.execute("""
                INSERT OR IGNORE INTO referrals
                    (referrer_tg_id, referred_tg_id, referral_code,
                     referred_username, referred_first_name, attributed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (profile["tg_id"], referred_tg_id, code, username or "", first_name or "", now))
            return db.execute(
                "SELECT * FROM referrals WHERE referred_tg_id = ?", (referred_tg_id,),
            ).fetchone()

    def prepare_reward(self, referred_tg_id, order_id, observed_expiry_ms, days=3):
        now = int(self.now_provider())
        now_ms = now * 1000
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            referral = db.execute(
                "SELECT * FROM referrals WHERE referred_tg_id = ?", (referred_tg_id,),
            ).fetchone()
            if referral is None:
                db.commit()
                return None
            if referral["qualifying_order_id"] not in (None, order_id):
                db.commit()
                return None
            if referral["qualifying_order_id"] is None:
                db.execute("""
                    UPDATE referrals SET status='qualified', qualified_at=?, qualifying_order_id=?
                    WHERE id=? AND qualifying_order_id IS NULL
                """, (now, order_id, referral["id"]))
            db.execute("""
                INSERT OR IGNORE INTO referral_rewards
                    (referral_id, recipient_tg_id, reward_type, amount, created_at)
                VALUES (?, ?, 'subscription_days', ?, ?)
            """, (referral["id"], referral["referrer_tg_id"], days, now))
            reward = db.execute(
                "SELECT * FROM referral_rewards WHERE referral_id=? AND reward_type='subscription_days'",
                (referral["id"],),
            ).fetchone()
            if reward["target_expiry_ms"] is None:
                previous = db.execute("""
                    SELECT MAX(target_expiry_ms) FROM referral_rewards
                    WHERE recipient_tg_id=? AND reward_type='subscription_days'
                """, (referral["referrer_tg_id"],)).fetchone()[0]
                target = (0 if observed_expiry_ms == 0 else
                          max(observed_expiry_ms, previous or 0, now_ms) + days * 86400000)
                db.execute("UPDATE referral_rewards SET target_expiry_ms=? WHERE id=?", (target, reward["id"]))
            db.commit()
            return db.execute("SELECT * FROM referral_rewards WHERE id=?", (reward["id"],)).fetchone()

    def mark_applied(self, reward_id):
        now = int(self.now_provider())
        with closing(self._connect()) as db, db:
            db.execute("UPDATE referral_rewards SET applied_at=COALESCE(applied_at, ?) WHERE id=?", (now, reward_id))
            db.execute("""
                UPDATE referrals SET status='rewarded'
                WHERE id=(SELECT referral_id FROM referral_rewards WHERE id=?)
            """, (reward_id,))
            return db.execute("SELECT * FROM referral_rewards WHERE id=?", (reward_id,)).fetchone()

    def mark_notified(self, reward_id):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE referral_rewards SET notified_at=COALESCE(notified_at, ?) WHERE id=?",
                       (int(self.now_provider()), reward_id))

    def stats(self, tg_id):
        with closing(self._connect()) as db:
            return db.execute("""
                SELECT COUNT(*) invited,
                  SUM(CASE WHEN qualified_at IS NOT NULL THEN 1 ELSE 0 END) qualified,
                  COALESCE((SELECT SUM(amount) FROM referral_rewards
                    WHERE recipient_tg_id=? AND reward_type='subscription_days'
                      AND applied_at IS NOT NULL), 0) earned_days
                FROM referrals WHERE referrer_tg_id=?
            """, (tg_id, tg_id)).fetchone()

    def list_referrals(self, tg_id):
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT * FROM referrals WHERE referrer_tg_id=? ORDER BY attributed_at DESC", (tg_id,),
            ).fetchall()

    def get_by_referred(self, tg_id):
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT * FROM referrals WHERE referred_tg_id=?", (tg_id,),
            ).fetchone()

    def list_rewards(self, tg_id):
        with closing(self._connect()) as db:
            return db.execute("""
                SELECT rw.*, r.referred_first_name, r.referred_username
                FROM referral_rewards rw JOIN referrals r ON r.id=rw.referral_id
                WHERE rw.recipient_tg_id=? AND rw.applied_at IS NOT NULL
                ORDER BY rw.applied_at DESC
            """, (tg_id,)).fetchall()
