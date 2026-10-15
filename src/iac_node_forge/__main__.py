"""Allows ``python -m iac_node_forge``."""

import sys

from iac_node_forge.presentation.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
