"""Constants for the Noo-Psyche K7 lamp integration."""
from datetime import timedelta

DOMAIN = "k7_lamp"
DEFAULT_PORT = 8266
SCAN_INTERVAL = timedelta(seconds=30)
ALL_SET_SETTLE = 4  # seconds the lamp is busy after a full schedule write
CLOCK_SYNC_DELAY = timedelta(minutes=5)  # after the daily plan write
STORAGE_VERSION = 1
FRONTEND_VERSION = "0.5.0"
URL_BASE = "/k7_lamp"
CARD_FILE = "k7-schedule-card.js"
