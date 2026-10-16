"""Strategy settings read from environment variables. Defaults reproduce the original (22-pip) behaviour."""
import os


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


BOX_SIZE = _f("BOX_SIZE", "0.0022")
BOX_PIPS = round(BOX_SIZE / 0.0001)
STOP_PIPS = _f("STOP_PIPS", "52")
EXIT_MODE = os.environ.get("EXIT_MODE", "system").strip().lower()      # system | ride_trail
if EXIT_MODE not in ("system", "ride_trail"):
    EXIT_MODE = "system"
ENTRY_TYPE = os.environ.get("ENTRY_TYPE", "any").strip().lower()       # any | reversal
if ENTRY_TYPE not in ("any", "reversal"):
    ENTRY_TYPE = "any"


def summary() -> dict:
    return {
        "box_size": BOX_SIZE, "box_pips": BOX_PIPS, "stop_pips": STOP_PIPS,
        "exit_mode": EXIT_MODE, "entry_type": ENTRY_TYPE,
        "trail_pips": 2 * BOX_PIPS,
    }
