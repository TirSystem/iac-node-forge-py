"""One module per command; each adds its parser and runs its use case."""

import argparse

type Subparsers = argparse._SubParsersAction[argparse.ArgumentParser]
"""The object a command registers its parser on."""
