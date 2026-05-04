# Dokployer

Dokployer is CLI tool that uploads interpolated Docker Swarm stack files to
Dokploy, updates or creates the target compose stack, waits until deployment
finishes, and can optionally verify container readiness.

It is designed for CI/CD usage where the stack YAML and Dokploy env file need
light templating from the current process environment before they are sent to
Dokploy.

## Features

- Deploy raw Docker Swarm stack YAML to Dokploy compose stacks.
- Expand only `$${VAR}` placeholders from the current process environment.
- Preserve Dokploy `${{...}}`, Docker Compose `${...}`, and shell `$VAR`
  placeholders unchanged.
- Optionally upload a Dokploy env file together with the stack.
- Always poll Dokploy until deploy status becomes `done`.
- Optionally wait until expected stack containers run the image specified in the
  stack file.
- Inspect Dokploy app, services, containers, and deployments through API-only
  read-only commands.
- Uses only the Dokploy HTTP API with `DOKPLOY_API_KEY`; it does not use SSH,
  Docker CLI, or host-level access.

## Requirements

- Python `3.13` or `3.14`
- Dokploy API access
- These environment variables:
  - `DOKPLOY_URL`
  - `DOKPLOY_API_KEY`
  - `DOKPLOY_ENV_ID`

App targeting:

- `DOKPLOY_APP_ID`
  - Dokploy `composeId`; wins over environment/name lookup.
- `DOKPLOY_ENV_ID` + `DOKPLOY_APP_NAME`
  - Used to resolve the app by name when `DOKPLOY_APP_ID` is not set.

Compatibility aliases:

- `DOKPLOY_ENVIRONMENT_ID` for `DOKPLOY_ENV_ID`
- `DOKPLOY_APP` for `DOKPLOY_APP_NAME`
- `DOKPLOY_SERVICE_ID` for `DOKPLOY_APP_ID`

If a canonical variable and its compatibility alias are both set to different
values, `dokployer` fails with a configuration error.

Optional runtime variables:

- `WAIT_TIMEOUT`
  - Max seconds to wait for Dokploy deploy status. Default: `300`.
- `WAIT_INTERVAL`
  - Polling interval in seconds for deploy and container readiness checks.
    Default: `5`.

`--wait` optionally enables container readiness checks after Dokploy reports the
deploy as `done`:

- `--wait`
  - Waits up to `60` seconds.
- `--wait 300`
  - Waits up to `300` seconds.

Container readiness is API-only. `dokployer` verifies that expected service
replicas are running with the interpolated `services.<name>.image` value.
Services used with `--wait` must define `image`; `deploy.mode: global` is not
supported because the expected replica count cannot be derived without Docker
node access.

## Placeholder Syntax

`dokployer` expands only placeholders in the form below:

- `$${VAR}`
  - strict; fails if `VAR` is missing
- `$${VAR:-}`
  - empty string when `VAR` is missing
- `$${VAR:-default}`
  - uses `default` when `VAR` is missing

Everything else is left unchanged:

- Dokploy templates: `${{environment.LOG_LEVEL}}`
- Docker Compose runtime variables: `${IMAGE}`
- Shell variables: `$IMAGE`

## Local Usage

Install dependencies:

```bash
uv sync
```

Run from the workspace:

```bash
uv run dokployer stack-name -f path/to/stack.yml --env path/to/dokploy.env --wait
```

Canonical deploy form:

```bash
uv run dokployer deploy app-name -f path/to/stack.yml --env path/to/dokploy.env --wait
```

Or install the package and run it directly:

```bash
uv tool install .
dokployer stack-name -f path/to/stack.yml --env path/to/dokploy.env --wait
```

You can also pipe the stack YAML through stdin:

```bash
cat path/to/stack.yml | uv run dokployer stack-name --env path/to/dokploy.env --wait
```

Read-only API inspection:

```bash
uv run dokployer inspect app
uv run dokployer inspect services
uv run dokployer inspect containers --running
uv run dokployer inspect deployments --limit 10
```

Inspection commands print tab-separated text by default. Add `--json` to print
JSON.

Inspection commands only use the Dokploy API. They do not use SSH, Docker CLI,
or host log streaming.

## Docker Usage

The GitHub workflow publishes:

- `ghcr.io/d1ys3nk0/dokployer:latest`
- `ghcr.io/d1ys3nk0/dokployer:<short-sha>`

When using `docker run`, mount the directory that contains the stack and env
files so the paths exist inside the container:

```bash
docker run --rm -i \
  -e DOKPLOY_URL \
  -e DOKPLOY_API_KEY \
  -e DOKPLOY_ENV_ID \
  -e DOKPLOY_APP_NAME \
  -e SERVICE_IMAGE \
  -v "$PWD:$PWD" \
  -w "$PWD" \
  ghcr.io/d1ys3nk0/dokployer:latest \
  dokployer stack-name -f path-to-swarm-stack.yml --env path/to/dotenv --wait
```

Important:

- `--env path/to/dotenv` is the Dokploy env file uploaded by `dokployer`.
- `-e DOKPLOY_*` and other `docker run -e ...` values are container process
  environment variables used for authentication and `$${VAR}` interpolation.

## GitLab CI Usage

This image is intended to work in jobs like:

```yaml
deploy:prd:
  image:
    name: ghcr.io/d1ys3nk0/dokployer:latest
    pull_policy: always
  script:
    - dokployer deploy ${SERVICE_WORLD}-${SERVICE_REALM}-${SERVICE_UNIT} -f .deploy/${SERVICE_UNIT}.stack.yml --env .deploy/_env_prd --wait
```

Because the image does not override the container entrypoint, CI shells can run
`dokployer` directly in job scripts.

## Development

Useful commands:

```bash
make setup
make fmt
make check
make build
make docker-build
```

## License

Apache-2.0. See [LICENSE](LICENSE).
