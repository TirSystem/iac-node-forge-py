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
`maconrad/proxmox` and runs privileged. Run it on your own machine only, never
in CI or on a shared host.

1. Set the container's password in the shell. Do not write it in a file.

   ```bash
   export PVE_PASSWORD='choose-a-password'
   ```

2. Start the container.

   ```bash
   docker compose -f tests/integration/docker-compose.proxmox.yml up -d
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
   docker compose -f tests/integration/docker-compose.proxmox.yml down
   ```

The token must never be committed or written to a file in the repository. The
tests never print it and only issue read requests.
