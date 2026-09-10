import argparse, asyncio, logging, os, sqlite3, sys, time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

try:
    from .app_config import ConfigError, load_config
    from .application import build_client_service
    from .models import is_mobile_email
    from .services import ClientServiceError
    from .telegram_format import markdown_v2_escape
except ImportError:
    from app_config import ConfigError, load_config
    from application import build_client_service
    from models import is_mobile_email
    from services import ClientServiceError
    from telegram_format import markdown_v2_escape

ENV_FILE = Path("/opt/karina-bot/.env")
DB_FILE = Path("/opt/karina-bot/karina.db")
LOCK_FILE = Path("/run/lock/karina-notifier.lock")
LOGGER = logging.getLogger(__name__)
DAY_MS = 86_400_000


@dataclass
class ScanSummary:
    users: int = 0
    bound: int = 0
    expiry_sent: int = 0
    quota_sent: int = 0
    duplicates: int = 0
    unbound: int = 0
    errors: int = 0


def load_env(path):
    data = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def db_connect(path=DB_FILE):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def init_db(connect=None):
    connect = connect or db_connect
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS notifications_sent (
          id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
          expiry_key TEXT NOT NULL, notification_day INTEGER NOT NULL,
          sent_at INTEGER NOT NULL, UNIQUE(email, expiry_key, notification_day));
        CREATE TABLE IF NOT EXISTS notification_quota_cycles (
          email TEXT PRIMARY KEY, cycle INTEGER NOT NULL, last_used INTEGER NOT NULL,
          quota INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        """)


def get_tg_id(email, connect=None):
    connect = connect or db_connect
    with connect() as db:
        row = db.execute("SELECT tg_id FROM telegram_links WHERE email = ?", (email,)).fetchone()
    if not row:
        return None
    if type(row[0]) is not int or row[0] <= 0:
        LOGGER.warning("Notifier binding is malformed email=%s", email)
        return None
    return row[0]


def event_sent(email, cycle_key, event, connect=None):
    connect = connect or db_connect
    with connect() as db:
        return db.execute("SELECT 1 FROM notifications_sent WHERE email=? AND expiry_key=? AND notification_day=?",
                          (email, cycle_key, event)).fetchone() is not None


def already_sent(email, expiry_time_ms, legacy_expiry_key, notification_day, connect=None):
    connect = connect or db_connect
    stable = str(expiry_time_ms)
    with connect() as db:
        return db.execute("SELECT 1 FROM notifications_sent WHERE email=? AND expiry_key IN (?,?,?) AND notification_day=?",
                          (email, stable, f"expiry:{stable}", legacy_expiry_key, notification_day)).fetchone() is not None


def mark_event_sent(email, cycle_key, event, connect=None, now_provider=None):
    connect = connect or db_connect
    now_provider = now_provider or (lambda: int(time.time()))
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO notifications_sent(email,expiry_key,notification_day,sent_at) VALUES(?,?,?,?)",
                   (email, cycle_key, event, int(now_provider())))


def mark_sent(email, expiry_time_ms, notification_day, connect=None, now_provider=None):
    mark_event_sent(email, str(expiry_time_ms), notification_day, connect, now_provider)


def notification_stage(days):
    if days <= 0: return 0
    if days <= 1: return 1
    if days <= 3: return 3
    if days <= 7: return 7
    return None


def expiry_stage(expiry_ms, now_ms):
    return notification_stage((expiry_ms - now_ms) / DAY_MS)


def quota_stage(used, quota):
    if quota <= 0 or used < quota * .8: return None
    if used >= quota: return 100
    if used >= quota * .95: return 95
    return 80


def quota_cycle(email, used, quota, connect=None, now_provider=None, write=True):
    connect = connect or db_connect
    now_provider = now_provider or (lambda: int(time.time()))
    with connect() as db:
        row = db.execute("SELECT cycle,last_used,quota FROM notification_quota_cycles WHERE email=?", (email,)).fetchone()
        cycle = 1 if row is None else int(row[0])
        if row is not None and (used < int(row[1]) or quota != int(row[2])):
            cycle += 1
        if write:
            db.execute("""INSERT INTO notification_quota_cycles(email,cycle,last_used,quota,updated_at)
              VALUES(?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET cycle=excluded.cycle,
              last_used=excluded.last_used,quota=excluded.quota,updated_at=excluded.updated_at""",
              (email, cycle, used, quota, int(now_provider())))
    return cycle


def _display_date(expiry_ms, timezone_name):
    try: zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc: raise ConfigError(f"unknown notifier timezone: {timezone_name}") from exc
    return datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).astimezone(zone).strftime("%d.%m.%Y")


def notification_text(stage, expiry_ms=None, timezone_name="Europe/Moscow"):
    lead = {7:"Подписка «Карина VPN» закончится через 7 дней.",3:"Подписка «Карина VPN» закончится через 3 дня.",
            1:"Подписка «Карина VPN» закончится завтра.",0:"Срок подписки «Карина VPN» закончился."}[stage]
    if expiry_ms: lead += f"\n\nДата окончания: {_display_date(expiry_ms, timezone_name)}"
    if stage in (7, 0): lead += "\n\nДля продолжения работы VPN продлите подписку."
    return markdown_v2_escape(lead)


def quota_text(stage, used, quota):
    used_gb, quota_gb, left_gb = used/1024**3, quota/1024**3, max(quota-used,0)/1024**3
    if stage == 100: text = "Лимит мобильного профиля «Карина против глушилок» исчерпан.\n\nОсновные VPN-подключения продолжают работать."
    elif stage == 95: text = f"В мобильном профиле «Карина против глушилок» осталось совсем немного трафика.\n\nИспользовано {used_gb:.1f} ГБ из {quota_gb:.0f} ГБ."
    else: text = f"Мобильный профиль «Карина против глушилок» использовал 80% доступного трафика.\n\nОсталось примерно {left_gb:.1f} ГБ из {quota_gb:.0f} ГБ."
    return markdown_v2_escape(text)


def notification_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("💳 Продлить VPN",callback_data="client_pay")],
                                 [InlineKeyboardButton("💗 Мой профиль",callback_data="client_home")]])


async def send_notification(bot, tg_id, kind, stage, **values):
    text = notification_text(stage, values.get("expiry_ms"), values.get("timezone_name","Europe/Moscow")) if kind == "expiry" else quota_text(stage, values["used"], values["quota"])
    await bot.send_message(chat_id=tg_id,text=text,reply_markup=notification_keyboard(),parse_mode=ParseMode.MARKDOWN_V2)


async def process_expiring_client(client, sender, connect=None, now_provider=None):
    if not client.enabled or client.expiry_time_ms <= 0: return "ineligible"
    stage = notification_stage(client.days_remaining)
    if stage is None: return "outside_window"
    tg_id = get_tg_id(client.email, connect)
    if not tg_id: return "not_linked"
    if already_sent(client.email,client.expiry_time_ms,client.expiry_text,stage,connect): return "already_sent"
    await sender(tg_id,stage)
    mark_sent(client.email,client.expiry_time_ms,stage,connect,now_provider)
    return "sent"


async def _process_user(client, service, sender, summary, connect, now_ms, now_provider, timezone_name, dry_run, emit):
    tg_id = get_tg_id(client.email, connect)
    if not tg_id: summary.unbound += 1; return
    summary.bound += 1
    if client.expiry_time_ms > 0:
        stage = expiry_stage(client.expiry_time_ms, now_ms)
        if stage is not None:
            key = f"expiry:{client.expiry_time_ms}"
            if event_sent(client.email,key,stage,connect): summary.duplicates += 1
            elif dry_run: emit(f"User: {client.email} Expiry: expiry_{stage}")
            else:
                await sender(tg_id,"expiry",stage,expiry_ms=client.expiry_time_ms,timezone_name=timezone_name)
                mark_event_sent(client.email,key,stage,connect,now_provider); summary.expiry_sent += 1
    traffic = service.get_mobile_traffic(client.email)
    if traffic is None: return
    cycle = quota_cycle(client.email,traffic.used_bytes,traffic.limit_bytes,connect,now_provider,not dry_run)
    stage = quota_stage(traffic.used_bytes,traffic.limit_bytes)
    if stage is None: return
    key = f"quota:{cycle}:{traffic.limit_bytes}"
    if event_sent(client.email,key,stage,connect): summary.duplicates += 1
    elif dry_run: emit(f"User: {client.email} Mobile: quota_{stage}")
    else:
        await sender(tg_id,"quota",stage,used=traffic.used_bytes,quota=traffic.limit_bytes)
        mark_event_sent(client.email,key,stage,connect,now_provider); summary.quota_sent += 1


async def run_notification_pass(service,sender,connect=None,now_provider=None,logger=None,admin_sender=None,admin_tg_id=None,dry_run=False,target_user=None,timezone_name="Europe/Moscow",emit=print):
    connect, now_provider, logger = connect or db_connect, now_provider or (lambda:int(time.time())), logger or LOGGER
    clients = service.list_clients()
    if target_user:
        if is_mobile_email(target_user): raise ValueError("internal mobile credential cannot be targeted")
        clients = [x for x in clients if x.email == target_user]
    summary = ScanSummary(users=len(clients)); now_ms = int(now_provider()*1000)
    for client in clients:
        try: await _process_user(client,service,sender,summary,connect,now_ms,now_provider,timezone_name,dry_run,emit)
        except Exception as exc:
            summary.errors += 1; logger.warning("Notifier user failed email=%s type=%s",client.email,type(exc).__name__,exc_info=True)
    logger.info("notifier scan complete users=%d bound=%d expiry_sent=%d quota_sent=%d duplicates=%d unbound=%d errors=%d",summary.users,summary.bound,summary.expiry_sent,summary.quota_sent,summary.duplicates,summary.unbound,summary.errors)
    if summary.errors and admin_sender and admin_tg_id:
        try: await admin_sender(admin_tg_id,markdown_v2_escape(f"⚠️ Karina notifier завершил проверку с ошибками.\n\nПроверено: {summary.users}\nУведомлено: {summary.expiry_sent+summary.quota_sent}\nОшибок: {summary.errors}\n\nПодробности записаны в журнал."))
        except Exception: logger.error("Notifier admin summary failed",exc_info=True)
    return summary


@contextmanager
def notifier_lock(path=LOCK_FILE):
    path.parent.mkdir(parents=True,exist_ok=True); handle=path.open("a+")
    try:
        if os.name == "posix":
            import fcntl
            try: fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: yield False; return
        yield True
    finally: handle.close()


def parse_args(argv=None):
    parser=argparse.ArgumentParser(prog="karina-notifier"); parser.add_argument("--dry-run",action="store_true"); parser.add_argument("--user"); return parser.parse_args(argv)


async def async_main(args):
    from telegram import Bot
    env=load_env(ENV_FILE); bot=Bot(env["BOT_TOKEN"])
    async def sender(tg_id,kind,stage,**values): await send_notification(bot,tg_id,kind,stage,**values)
    async def admin_sender(tg_id,text): await bot.send_message(chat_id=tg_id,text=text,parse_mode=ParseMode.MARKDOWN_V2)
    try:
        init_db(); config=load_config(); service=build_client_service()
        with notifier_lock() as acquired:
            if not acquired: LOGGER.warning("Notifier scan skipped: another run holds the lock"); return 0
            result=await run_notification_pass(service,sender,admin_sender=admin_sender,admin_tg_id=int(env["ADMIN_TG_ID"]),dry_run=args.dry_run,target_user=args.user,timezone_name=config.notifier_timezone)
    except Exception:
        LOGGER.error("Notifier global scan failure",exc_info=True)
        try:
            await admin_sender(int(env["ADMIN_TG_ID"]),markdown_v2_escape(
                "⚠️ Karina notifier не смог выполнить проверку. Подробности записаны в журнал."))
        except Exception:
            LOGGER.error("Notifier global failure alert failed",exc_info=True)
        raise
    return 1 if result.errors else 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO); args=parse_args(argv)
    if args.user and is_mobile_email(args.user): print("Error: internal mobile credential cannot be targeted",file=sys.stderr); return 2
    try: return asyncio.run(async_main(args))
    except Exception as exc:
        LOGGER.error("Notifier infrastructure failure (%s)",type(exc).__name__,exc_info=True); return 1


if __name__ == "__main__": raise SystemExit(main())
