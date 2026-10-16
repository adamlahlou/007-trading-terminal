"""Patch 17: run the tested system LIVE, switched on by settings (defaults = today's behaviour).

New settings (Render > Environment). With none set, NOTHING changes:
  BOX_SIZE   0.0022   -> 0.0030 for the 30-pip box          (already existed)
  STOP_PIPS  52       -> 60                                   (new: initial stop AND position sizing)
  EXIT_MODE  system   -> ride_trail  (no reversal-brick exit; only the stop/trail closes a trade)
  ENTRY_TYPE any      -> reversal    (flat entries only on a brick that reverses direction)
  GATE_SOURCE count   -> change      (the change-based gauge verdict, patch 16)

Also: /api/backtest accepts box_size, and /api/strategy-config shows the active settings plus whether the paper
and real positions are flat (switch when both are flat).
Nothing is written unless every edit matches exactly once."""
import pathlib, sys

CONFIG = '''"""Strategy settings read from environment variables. Defaults reproduce the original (22-pip) behaviour."""
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
'''

edits = {
 "app/live_trader.py": [
  ("from .backtest import INITIAL_STOP_PIPS, BREAKEVEN_AFTER_BRICKS, WIDE_TRAIL_BOXES, PIP\n",
   "from .backtest import BREAKEVEN_AFTER_BRICKS, WIDE_TRAIL_BOXES, PIP\nfrom . import strategy_config\n"),
  ("    initial_stop_dist = INITIAL_STOP_PIPS * PIP\n    events = []\n",
   "    initial_stop_dist = strategy_config.STOP_PIPS * PIP\n    events = []\n"
   "    ride_trail = strategy_config.EXIT_MODE == \"ride_trail\"\n"
   "    reversal_only = strategy_config.ENTRY_TYPE == \"reversal\"\n"
   "    # direction of the brick just before this scan's first new brick (for reversal-only entries)\n"
   "    last_dir = None\n"
   "    for _c, _bs in candle_brick_groups:\n"
   "        if _bs:\n"
   "            last_dir = db.get_brick_direction_before(_bs[0][1])\n"
   "            break\n"),
  ("        for b, seq in bricks_with_seq:\n            if position == 0:\n                if not _gauges_support(b.direction):\n",
   "        for b, seq in bricks_with_seq:\n            prev_dir, last_dir = last_dir, b.direction\n            if position == 0:\n"
   "                if reversal_only and not (prev_dir is not None and prev_dir != b.direction):\n"
   "                    continue  # only enter on a brick that reverses the previous direction\n"
   "                if not _gauges_support(b.direction):\n"),
  ("                    stop_price = max(stop_price, candidate_stop) if position == 1 else min(stop_price, candidate_stop)\n            else:\n",
   "                    stop_price = max(stop_price, candidate_stop) if position == 1 else min(stop_price, candidate_stop)\n"
   "                if ride_trail:\n"
   "                    # re-check the stop against this same candle right after moving it (same as the backtest)\n"
   "                    if position == 1 and candle[\"low\"] <= stop_price:\n"
   "                        close(stop_price, b.formed_at, seq, \"stop\")\n"
   "                    elif position == -1 and candle[\"high\"] >= stop_price:\n"
   "                        close(stop_price, b.formed_at, seq, \"stop\")\n"
   "            else:\n                if ride_trail:\n                    continue  # a reversal brick does not close the trade; only the stop/trail does\n"),
 ],
 "app/db.py": [
  ("def get_recent_bricks(limit: int = 200) -> list[dict]:\n",
   "def get_brick_direction_before(seq: int):\n"
   "    \"\"\"Direction of the brick immediately before brick `seq` (None if there isn't one).\"\"\"\n"
   "    conn = get_conn()\n"
   "    row = conn.execute(\"SELECT direction FROM bricks WHERE seq < ? ORDER BY seq DESC LIMIT 1\", (seq,)).fetchone()\n"
   "    conn.close()\n"
   "    return row[\"direction\"] if row else None\n\n\n"
   "def get_recent_bricks(limit: int = 200) -> list[dict]:\n"),
 ],
 "app/live_execution.py": [
  ("INITIAL_STOP_PIPS = 52  # matches backtest.py / live_trader.py exactly\n",
   "from .strategy_config import STOP_PIPS as INITIAL_STOP_PIPS  # stop + sizing follow the STOP_PIPS setting (default 52)\n"),
 ],
 "app/macro_llm.py": [
  ("def _price_context() -> dict:\n    bricks = db.get_recent_bricks(limit=24)\n",
   "def _price_context() -> dict:\n    from . import strategy_config\n    bricks = db.get_recent_bricks(limit=24)\n"),
  ("        \"brick_size_pips\": 22,\n    }\n\n\ndef build_inputs",
   "        \"brick_size_pips\": strategy_config.BOX_PIPS,\n    }\n\n\ndef build_inputs"),
 ],
 "app/main.py": [
  ("stop_pips: float = 52.0, entry_type: str = \"any\"):",
   "stop_pips: float = 52.0, entry_type: str = \"any\", box_size: float = 0.0022):"),
  ("backtest.run_backtest, days, 0.0022, reversal_only,", "backtest.run_backtest, days, box_size, reversal_only,"),
  ("@app.get(\"/api/live-trades\")\n",
   "@app.get(\"/api/strategy-config\")\n"
   "async def api_strategy_config():\n"
   "    from . import strategy_config\n"
   "    st = db.get_live_trade_state()\n"
   "    paper = st[\"position\"] if st else 0\n"
   "    try:\n"
   "        rp = await asyncio.to_thread(oanda_execution.get_open_position)\n"
   "        real_flat = not rp or (not rp.get(\"long_units\") and not rp.get(\"short_units\"))\n"
   "    except Exception as e:\n"
   "        rp, real_flat = {\"error\": str(e)}, None\n"
   "    return JSONResponse({**strategy_config.summary(), \"gate_source\": macro_llm.gate_source(),\n"
   "                         \"paper_position\": paper, \"paper_flat\": paper == 0,\n"
   "                         \"real_position\": rp, \"real_flat\": real_flat,\n"
   "                         \"safe_to_switch\": paper == 0 and real_flat is True})\n\n\n"
   "@app.get(\"/api/live-trades\")\n"),
 ],
}

new = {}
for path, reps in edits.items():
    t = pathlib.Path(path).read_text()
    for old, rep in reps:
        c = t.count(old)
        if c != 1:
            print(f"FAILED on {path}: expected 1 match, found {c}, for:\n    {old[:90]!r}"); sys.exit(1)
        t = t.replace(old, rep)
    new[path] = t
if pathlib.Path("app/strategy_config.py").exists():
    print("FAILED: app/strategy_config.py already exists -- looks already applied"); sys.exit(1)
for p, t in new.items():
    pathlib.Path(p).write_text(t); print("patched", p)
pathlib.Path("app/strategy_config.py").write_text(CONFIG); print("added app/strategy_config.py")
print("\nPatch 17 applied cleanly. Nothing changes until you set the new variables on Render.")
