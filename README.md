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

## Integration tests against a real Proxmox VE

The tests in `tests/integration` run the Proxmox VE adapter against a real
server. They only read. They are marked `integration`, are deselected by a
plain `pytest`, and are skipped when `PVE_TOKEN` is not set, so CI is not
affected.

Prerequisites: Docker. The container uses the third-party community image
`dockurr/proxmox` and runs privileged. Run it on your own machine only, never
in CI or on a shared host.

1. Put the container's password in the git-ignored file
   `tests/integration/pve.env`. Copy the example and edit it; never commit it.

   ```bash
   cp tests/integration/pve.env.example tests/integration/pve.env
   # edit tests/integration/pve.env: PVE_PASSWORD=<choose a password>
   ```

2. Start the container, passing that file to compose.

   ```bash
   docker compose --env-file tests/integration/pve.env -f tests/integration/docker-compose.proxmox.yml up -d
   ```

   Some images ignore `PVE_PASSWORD`. Set root's password from the value the
   container received, through standard input:

   ```bash
   docker exec proxmox-node1 sh -c 'echo "root:$PVE_PASSWORD" | chpasswd'
   ```

3. Wait until `https://localhost:8006` answers (the certificate is
   self-signed, so the browser warns).

4. Create an API token inside the container. The output contains the full
   value `root@pam!iacnf=<secret>`; the secret is shown only once.

   ```bash
   docker exec proxmox-node1 pveum user token add root@pam iacnf --privsep 0
   ```

5. Put the token in the environment and run the tests. `PVE_VERIFY=false` is
   needed because of the self-signed certificate; `PVE_VERIFY` may also be a
   path to a CA bundle, and `PVE_ADDRESS` defaults to `https://localhost:8006`.

   ```bash
   export PVE_TOKEN='root@pam!iacnf=<secret>'
   export PVE_VERIFY=false
   pytest -m integration
   ```

6. Tear down.

   ```bash
   docker compose --env-file tests/integration/pve.env -f tests/integration/docker-compose.proxmox.yml down
   ```

   Delete `tests/integration/pve.env` when you no longer need it.

The token must never be committed or written to a file in the repository. The
tests never print it and only issue read requests.
