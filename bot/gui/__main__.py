"""Depuis la racine du projet : .venv\\Scripts\\pythonw -m bot.gui (sans console), ou python -m bot.gui."""

import sys

from bot.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
