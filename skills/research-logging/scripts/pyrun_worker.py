"""Register an ordinary worker before replacing it with the research command."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from research_log_reservations import register_worker


def main(arguments: list[str]) -> None:
    """Register the parent-created reservation, then exec the supplied command."""

    root, identity, *command = arguments
    register_worker(Path(root), identity)
    os.execv(command[0], command)


if __name__ == "__main__":
    main(sys.argv[1:])
