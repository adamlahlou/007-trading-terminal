"""
Lets scanner.py/live_execution.py schedule delayed jobs (the 3-min
realistic execution delay) without needing to import main.py directly,
which would create a circular import (main.py already imports from
scanner.py). main.py registers its scheduler here once at startup; anyone
else just asks for it when they need it.
"""
_scheduler = None


def set_scheduler(s):
    global _scheduler
    _scheduler = s


def get_scheduler():
    return _scheduler
