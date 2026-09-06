"""``python3 -m trigger_warnings`` runs the same entry point as the installed command."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
