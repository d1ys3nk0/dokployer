# Contributing

## Setup

```bash
make setup
```

## Before Opening a Pull Request

Run the full local check suite:

```bash
make check
```

Build the package artifacts:

```bash
make build
```

Optionally validate the image locally:

```bash
make docker-build
```

## Development Notes

- Keep the CLI contract stable unless the change explicitly requires a breaking
  change.
- Prefer small, reviewable changes.
- Add or update tests together with behavior changes.
