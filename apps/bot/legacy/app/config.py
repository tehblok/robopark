from paths import DATA_DIR as _DATA_DIR
from telegram_chats import get_chats, resolve_profile

QUEUE = "SDCFLEETOPS"
DATA_DIR = str(_DATA_DIR)

# Telegram: prod или test — см. telegram_chats.py, switch_telegram.py
# Prefer store getters at call time; these aliases remain for legacy imports.
TELEGRAM_PROFILE = resolve_profile()


def _live_chats():
    try:
        from store.locations import chats, logistics_chats

        return chats(), logistics_chats()
    except Exception:
        return get_chats(resolve_profile())


TELEGRAM_CHATS, TELEGRAM_LOGISTICS_CHATS = _live_chats()


def refresh_telegram_chats() -> None:
    global TELEGRAM_CHATS, TELEGRAM_LOGISTICS_CHATS, TELEGRAM_PROFILE
    TELEGRAM_PROFILE = resolve_profile()
    TELEGRAM_CHATS, TELEGRAM_LOGISTICS_CHATS = _live_chats()

LOGISTICS_ENABLED = False

# KillSwitch: donut PNG перед табличкой (Next, Север, Сигма, Континент)
# Кампания окончена — диаграммы не шлём.
KILLSWITCH_PROGRESS_ENABLED = False

# Диспетчер-бот: whitelist в data/dispatcher_users.json (bootstrap из DISPATCHER_USERS).
#
# Роли (см. dispatcher_roles.py):
#   admin    — только DISPATCHER_ADMIN_IDS; админ-команды + global-поиск
#   operator — role "operator", allowed_tags "*"; global-поиск роботов
#   mechanic — role "mechanic", allowed_tags [тег локации]; якорь по локации
#
# Якорь механика: если хотя бы одна задача на роботе имеет тег из allowed_tags —
# показываются все активные задачи на этом роботе.
PINNED_ADMIN_IDS = ()
DISPATCHER_ADMIN_IDS = []

DISPATCHER_DEFAULT_GREETING = (
    "{name}, отправь номер робота (например 1842) — покажу активные задачи."
)

DISPATCHER_USERS = {}
