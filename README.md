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
`tests/contract` and `tests/e2e`.

## Set up

Python 3.13 is required.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate   Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Usage

The client is read-only: it never creates, changes or deletes anything at a
provider. Installing the package gives the `iac-node-forge` command;
`python -m iac_node_forge` is the same.

```bash
iac-node-forge provider add --address https://pve.example:8006 --name Lab
iac-node-forge provider add --cache-credential   # asks for address and name
iac-node-forge capabilities Lab                  # supported and unsupported actions
iac-node-forge options Lab node                  # option kinds: node, storage
iac-node-forge servers                           # all configurations
iac-node-forge servers --configuration Lab
iac-node-forge server Lab 100
```

A configuration is named by its display name (case is ignored) or its id.
`provider add` asks for the Proxmox VE API token (`USER@REALM!TOKENID=SECRET`)
without echo, checks it with the provider and saves the configuration. The
token is never written to the configuration file; with `--cache-credential` it
is kept in the platform credential store, and later commands use it before
asking again.

Environment variables:

| Variable | Meaning |
| --- | --- |
| `IAC_NODE_FORGE_CONFIG` | Path of the configuration file (JSON Lines). Default: `%APPDATA%\iac-node-forge\configuration.jsonl` on Windows, `$XDG_CONFIG_HOME` or `~/.config` under `iac-node-forge/` elsewhere. |
| `IAC_NODE_FORGE_VERIFY` | TLS verification: `true` (default), `false` (self-signed certificate; a warning is printed) or the path of a CA bundle file. |

Output is plain text on standard output; errors go to standard error.

| Exit code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | A failure you can act on: provider unreachable or token rejected, configuration or server not found, unknown option kind, nothing configured, a provider failed in `servers` |
| 2 | Usage error |

## Checks

```bash
ruff check .
ruff format --check .
mypy src tests
lint-imports
pytest
```
