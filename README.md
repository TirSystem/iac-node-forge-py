# iac-node-forge-py

Python command-line client of iac-node-forge, which manages servers at
Proxmox VE and Contabo.

## Layers

The package `iac_node_forge` lives under `src/` and is planned in four layers.
A layer folder is created when its first file is added.

- `domain`: entities, value objects and business rules; no input or output.
- `application`: use cases and the ports they need from outside.
- `infrastructure`: implementations of ports, configuration and drivers.
- `presentation`: the command line and the composition root.

Dependencies point inward: `presentation` and `infrastructure` depend on
`application`, which depends on `domain`. Tests are in `tests/unit`,
`tests/integration` and `tests/e2e`.

## Set up

Python 3.13 is required.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate   Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Checks

```bash
ruff check .
ruff format --check .
mypy src
pytest
```
