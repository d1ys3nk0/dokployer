# Contributing

## Setup

```bash
task setup
```

## Before Opening a Pull Request

Run the full local check suite:

```bash
task check
```

Build the package artifacts:

```bash
task build
```

Optionally validate the image locally:

```bash
task docker-build
```

## Development Notes

- Keep the CLI contract stable unless the change explicitly requires a breaking
  change.
- Prefer small, reviewable changes.
- Add or update tests together with behavior changes.
