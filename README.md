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

The client reads servers and options, and it can start, shut down, stop and
restart a server, create a server from a saved description and delete or cancel
one. It never reinstalls anything at a provider: the only requests that change
something are the four power actions, the create request and the removal
request, and each of them needs your confirmation first. Installing the package gives the `iac-node-forge` command;
`python -m iac_node_forge` is the same.

```bash
iac-node-forge provider add --address https://pve.example:8006 --name Lab
iac-node-forge provider add --cache-credential   # asks for address and name
iac-node-forge provider add --kind contabo --name Cloud   # address defaults to https://api.contabo.com
iac-node-forge capabilities Lab                  # supported and unsupported actions
iac-node-forge options Lab node                  # Proxmox VE: node, storage
iac-node-forge options Cloud region              # Contabo: region, image
iac-node-forge servers                           # all configurations
iac-node-forge servers --configuration Lab
iac-node-forge server Lab 100                    # details of one server
iac-node-forge server start Lab 101              # power on (asks first)
iac-node-forge server shutdown Lab 100           # graceful shutdown
iac-node-forge server stop Lab 100               # power off at once
iac-node-forge server restart Lab 100 --yes --confirm-unmanaged 100
iac-node-forge server stop Cloud 100 --no-wait   # prints the operation, exits 0
iac-node-forge operation <operation-id>          # a recorded operation
iac-node-forge operation <operation-id> --follow # follow one that is running
iac-node-forge server define Lab web --image 'pve1/local:vztmpl/alpine.tar.xz' --key deploy --set kind=lxc --set node=pve1 --set storage=pve1/local-lvm --set memory_mb=512 --set cores=1 --set disk_gb=4
iac-node-forge server definitions Lab --check    # the saved descriptions
iac-node-forge server provision Lab web --name web-2   # shows the cost, asks, creates
iac-node-forge server remove Lab 101             # states the consequence, asks you to type the id
iac-node-forge server remove Cloud 101 --yes --confirm-unmanaged 101
```

A configuration is named by its display name (case is ignored) or its id.
`provider add` takes `--kind proxmox_ve` (the default) or `--kind contabo`, asks
for the credentials of that provider without echo, checks them with the
provider and saves the configuration.

| Provider | Credentials asked for, in order | Address |
| --- | --- | --- |
| Proxmox VE | `token`: the API token, `USER@REALM!TOKENID=SECRET` | asked for, such as `https://pve.example:8006` |
| Contabo | `client_id`, `client_secret`, `api_user`, `api_password` from the customer control panel | `https://api.contabo.com`; `--address` overrides it, for a proxy |

No credential is ever written to the configuration file. With
`--cache-credential` the values are kept as one entry of the platform credential
store, and later commands use it before asking again. An entry that cannot be
read is ignored: one line on standard error names its reference and the
credentials are asked for instead.

`options` takes the kinds of the configuration's provider: `node` and
`storage` for Proxmox VE, `region` and `image` for Contabo. Another kind exits
with 1 and lists the valid kinds. `servers` lists every configuration, Proxmox VE
and Contabo together; a provider that fails is reported and does not hide the
servers of the others.

### Power commands

`server start|shutdown|stop|restart <configuration> <identifier>` changes the
power state of one server and follows the operation to its end (UC-008,
UC-006). The word `server` is shared with the read command: a line of exactly
`server <configuration> <identifier>` still shows a server, and a verb followed
by a configuration and an identifier is a power command. (A configuration
named `start` is still shown by `server start 100`; its servers are controlled
with `server stop start 100`.)

| Option | Meaning |
| --- | --- |
| `--yes` | Confirm the action without asking. |
| `--confirm-unmanaged <identifier>` | The extra confirmation for a server this client does not manage: the identifier of that server. |
| `--no-wait` | Print the operation (identifier and state `running`) and exit 0. |
| `--timeout <seconds>` | Longest time to follow the operation (default 120). |
| `--poll-interval <seconds>` | Time between two reads of the operation (default 2). |

Confirmation rules (ADR-0006):

- Every action needs a confirmation before any request is sent. On a terminal
  the client states the action (for `stop` and `restart` that unsaved data may
  be lost, and for a server not managed by this client that it is not), asks
  yes or no and, for an unmanaged server, asks to retype the identifier.
- Without a terminal on standard input nothing is asked: `--yes` is required
  and, for an unmanaged server, `--confirm-unmanaged <identifier>` with the
  identifier of that server. Without them the exit code is 1 and no request is
  sent. A server this client created with `server provision` is recorded as
  managed; every other server needs the extra confirmation.
- The state is read first. `start` of a running server and `shutdown` or `stop`
  of a stopped one are reported as already in that state, with exit code 1; no
  request is sent and nothing is recorded.
- An action the provider does not support is reported as unsupported, with
  exit code 1, before anything is asked or sent.

The operation ends as `succeeded` (exit 0, with the state the server has now),
`failed` (the provider's cause is shown, exit 1) or `unknown` (the time limit
passed or the provider could not be asked, exit 1). An unknown outcome is
never shown as success and the request is never repeated.

### Define and provision

`server define <configuration> <name> --image IMAGE [--key NAME] [--hint TEXT]
[--set NAME=VALUE]` checks a description of the server wanted against what the
provider offers and saves it as a named template (UC-003, ADR-0007). The image is
the template (Proxmox VE) or the image (Contabo) as `options` shows it; the
provider-specific values are `kind`, `node`, `storage`, `memory_mb`, `cores` and
`disk_gb` for Proxmox VE and `region`, `product` and `period` (1, 12 or 24
months) for Contabo. A description that is not valid is reported, with each
problem, and not saved (exit 1); one with the same name is replaced. `--key` names
an access key to install and never holds its value. `server definitions
<configuration>` lists them; `--check` checks each against the provider now.

`server provision <configuration> <name> [--name SERVER_NAME] [--yes]
[--no-wait] [--timeout S] [--poll-interval S]` creates one server from a
description (UC-004). It shows the provider, target, image, size, values and the
cost consequence (for Contabo the contract period and that the account is
committed to it), asks for one yes (`--yes` without a terminal), saves an intent,
sends one create request and follows the operation. When it succeeds the server
is recorded as managed. A name that already exists at the provider, or an earlier
request for the name that is not finished (unknown outcome, lost answer), is
reported and nothing is sent. A rejected or failed creation reports the state left
at the provider. `operation <id>` completes a provision that has ended.

### Delete or cancel a server

`server remove <configuration> <identifier> [--yes] [--confirm-unmanaged ID]
[--no-wait] [--timeout S] [--poll-interval S]` removes one server (UC-012,
ADR-0008). A removal cannot be undone, so the command always prints the
preview first: the provider, the server with its name and state, whether this
client manages it, and what removal means at that provider. Proxmox VE destroys
the server at once and deletes its disks. Contabo only requests a cancellation:
the contract and its billing run on until the effective date Contabo reports
after the request, and the client cannot withdraw it. The date is shown in the
detail of the operation, never computed by the client.

Safety rules:

- The confirmation is typing the server identifier exactly as the provider
  names it; a name or a prefix is refused. A server this client does not manage
  (one not created with `server provision`) needs the identifier typed a second
  time. At a terminal both are asked for, whatever `--yes` says.
- Without a terminal nothing is asked: `--yes` is required, the identifier on
  the command line counts as the typed identifier and, for an unmanaged server,
  `--confirm-unmanaged <identifier>` is the second typing. Without them the exit
  code is 1 and no request is sent.
- A running Proxmox VE server is refused and never stopped for you: shut it down
  first. A Contabo instance that is already cancelled is reported, and so is a
  server the provider does not have. Nothing is sent or recorded in these cases.
- Exactly one removal request is sent. A rejection ends the operation as
  `failed` with the state left at the provider; a lost answer or a time limit
  ends it as `unknown`; the request is never repeated.
- The consequence text shown is recorded with the operation. When the operation
  succeeds, the managed-server record is ended (kept, with the time and the
  operation that ended it) and no longer counts as managed.
  `operation <id>` completes a removal that has ended.

### Operation record

Every power, provision or removal request is recorded as one `operation` line of
the same configuration file (identifier, configuration, server, action, state,
start and end time, the provider's task reference, a bounded detail text and,
for a removal, the consequence text). It never
holds a credential, a token or a header. `iac-node-forge operation <id>` shows
it, also from another process.

Environment variables:

| Variable | Meaning |
| --- | --- |
| `IAC_NODE_FORGE_CONFIG` | Path of the configuration file (JSON Lines). Default: `%APPDATA%\iac-node-forge\configuration.jsonl` on Windows, `$XDG_CONFIG_HOME` or `~/.config` under `iac-node-forge/` elsewhere. |
| `IAC_NODE_FORGE_VERIFY` | TLS verification: `true` (default), `false` (self-signed certificate; a warning is printed) or the path of a CA bundle file. |

Output is plain text on standard output; errors go to standard error.

| Exit code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | A failure you can act on: provider unreachable or credentials rejected (Proxmox VE: API token; Contabo: client ID, client secret, API user and API password), configuration, server or operation not found, unknown option kind for the provider, nothing configured, a provider failed in `servers`; for a power command also an unsupported action, a server already in the state, a missing or refused confirmation, a request the provider rejected, and an operation that ended `failed` or `unknown`; for `server define` a description that is not valid; for `server provision` an invalid or unsupported description, an existing server name or an unfinished earlier request, a missing confirmation, a rejected or failed creation; for `server remove` an unsupported removal, a running or already removed server, a wrong typed identifier, a missing or refused confirmation, a rejected or failed removal |
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

## Integration tests against a real Contabo account

The tests in `tests/integration/test_contabo_live.py` run the Contabo adapter
against your real Contabo account. They only read (one token request, then GET
requests) and cost nothing: no instance is created, changed, started, stopped,
reinstalled or cancelled. They are marked `integration`, are deselected by a
plain `pytest`, and are skipped unless all four required variables are set.

| Variable | Meaning |
| --- | --- |
| `CONTABO_CLIENT_ID` | OAuth2 client id |
| `CONTABO_CLIENT_SECRET` | OAuth2 client secret |
| `CONTABO_API_USER` | API user (the account e-mail address) |
| `CONTABO_API_PASSWORD` | API password |
| `CONTABO_API_URL` | optional; defaults to `https://api.contabo.com` |

Set the values without echoing them, then run the tests.

bash:

```bash
read -rsp 'Client secret: ' CONTABO_CLIENT_SECRET && export CONTABO_CLIENT_SECRET
read -rsp 'API password: ' CONTABO_API_PASSWORD && export CONTABO_API_PASSWORD
export CONTABO_CLIENT_ID='<client id>' CONTABO_API_USER='<api user>'
pytest -m integration -k contabo -v
```

PowerShell:

```powershell
$env:CONTABO_CLIENT_ID = '<client id>'
$env:CONTABO_API_USER = '<api user>'
$env:CONTABO_CLIENT_SECRET = Read-Host 'Client secret' -AsSecureString | ConvertFrom-SecureString -AsPlainText
$env:CONTABO_API_PASSWORD = Read-Host 'API password' -AsSecureString | ConvertFrom-SecureString -AsPlainText
pytest -m integration -k contabo -v
```

The values must never be committed or written to a file in the repository, and
the tests never print them. See the wiki guide `Contabo-Live-Test`.

## Power control live test (turns a server off and on)

WARNING: `tests/integration/test_power_live.py` really shuts down, starts and
restarts one server. Use only a DISPOSABLE server, never one that serves
anything. The test puts the server back into its original power state when it
ends, also when an assertion fails, but a failure of the provider can leave it
off. Nothing else is changed: no server is created, deleted, reinstalled or
snapshotted. The hard power off (`stop`) can lose unsaved data and runs only
when `POWER_TEST_ALLOW_HARD_STOP=1` is set as well.

Each provider is opted in on its own. Nothing runs unless every variable of
that provider is set; the skip reason names the missing ones, never a value.

| Variable | Meaning |
| --- | --- |
| `PVE_TOKEN`, `PVE_ADDRESS`, `PVE_VERIFY` | as in the Proxmox VE live test |
| `PVE_POWER_TEST_SERVER` | the vmid of the disposable server |
| `PVE_POWER_TEST_CONFIRM` | exactly `turn-off-server-<vmid>` |
| the four `CONTABO_` variables, `CONTABO_API_URL` | as in the Contabo live test |
| `CONTABO_POWER_TEST_SERVER` | the instance id of the disposable server |
| `CONTABO_POWER_TEST_CONFIRM` | exactly `turn-off-server-<instance id>` |
| `POWER_TEST_ALLOW_HARD_STOP` | `1` to also run the hard power off test |

bash (Proxmox VE):

```bash
read -rsp 'PVE token: ' PVE_TOKEN && export PVE_TOKEN
export PVE_POWER_TEST_SERVER='<vmid>' PVE_POWER_TEST_CONFIRM='turn-off-server-<vmid>'
pytest -m integration -k "power and proxmox" -v
```

PowerShell (Proxmox VE):

```powershell
$env:PVE_TOKEN = Read-Host 'PVE token' -AsSecureString | ConvertFrom-SecureString -AsPlainText
$env:PVE_POWER_TEST_SERVER = '<vmid>'
$env:PVE_POWER_TEST_CONFIRM = 'turn-off-server-<vmid>'
pytest -m integration -k "power and proxmox" -v
```

For Contabo, set the four `CONTABO_` variables as in the section above (silent
input for the client secret and the API password), then
`CONTABO_POWER_TEST_SERVER` and `CONTABO_POWER_TEST_CONFIRM`, and run
`pytest -m integration -k "power and contabo" -v`. See the wiki guide
`Power-Control-Live-Test`.

## Provisioning and removal live test (creates a container)

WARNING: `tests/integration/test_provision_live.py` really creates one LXC
container in a Proxmox VE and then removes it through the client's own removal
(preview, a refused request, the confirmed request with the typed identifier,
tracking and completion). Use only the disposable Docker
Proxmox VE of `tests/integration/docker-compose.proxmox.yml`, never a Proxmox VE
that serves anything. Only Proxmox VE is tested live; Contabo is never created
live because it costs money.

The container is named `iacnf-live-<8 hex>`, new on every run. A finalizer
removes only the container this run created, through the same removal, also
when an assertion fails; the test sends no raw delete. Nothing runs
unless every variable is set; the skip reason names the missing ones, never a
value.

| Variable | Meaning |
| --- | --- |
| `PVE_TOKEN`, `PVE_ADDRESS`, `PVE_VERIFY` | as in the Proxmox VE live test |
| `PVE_PROVISION_TEST_NODE` | the node name, such as the one of the Docker Proxmox VE |
| `PVE_PROVISION_TEST_STORAGE` | the storage of the root disk, such as `local` |
| `PVE_PROVISION_TEST_TEMPLATE` | a container template on that node, such as `local:vztmpl/alpine-3.24-default_20260714_amd64.tar.xz` |
| `PVE_PROVISION_TEST_CONFIRM` | exactly `create-server-iacnf-live` |

bash:

```bash
read -rsp 'PVE token: ' PVE_TOKEN && export PVE_TOKEN
export PVE_PROVISION_TEST_NODE='<node>' PVE_PROVISION_TEST_STORAGE='local'
export PVE_PROVISION_TEST_TEMPLATE='local:vztmpl/<template>'
export PVE_PROVISION_TEST_CONFIRM='create-server-iacnf-live'
pytest -m integration -k provision -v
```

PowerShell:

```powershell
$env:PVE_TOKEN = Read-Host 'PVE token' -AsSecureString | ConvertFrom-SecureString -AsPlainText
$env:PVE_PROVISION_TEST_NODE = '<node>'
$env:PVE_PROVISION_TEST_STORAGE = 'local'
$env:PVE_PROVISION_TEST_TEMPLATE = 'local:vztmpl/<template>'
$env:PVE_PROVISION_TEST_CONFIRM = 'create-server-iacnf-live'
pytest -m integration -k provision -v
```
