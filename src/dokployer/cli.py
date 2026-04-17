"""CLI for dokployer."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dokployer.dokploy_client import DokployClient
from dokployer.errors import DokployerError
from dokployer.stack_deployer import StackDeployer
from dokployer.template_manager import ComposeTemplate


def _configure_logging(*, verbose: bool, quiet: bool) -> None:
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run the deployment."""
    parser = argparse.ArgumentParser(
        prog="dokployer",
        description="Upload an interpolated Docker Swarm stack to Dokploy.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug output.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress informational output.",
    )
    parser.add_argument(
        "stack_name",
        help="Dokploy compose stack name",
    )
    parser.add_argument(
        "-f",
        "--compose-template",
        dest="template_path",
        type=Path,
        help="Path to the stack YAML template. Reads stdin when omitted.",
    )
    parser.add_argument(
        "--env",
        dest="env_template_path",
        type=Path,
        help="Optional Dokploy env file template to upload with the stack.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll Dokploy until the deploy finishes or times out.",
    )
    args = parser.parse_args(argv)

    _configure_logging(verbose=args.verbose, quiet=args.quiet)

    template = ComposeTemplate()
    client = DokployClient()
    deployer = StackDeployer(client, template)

    try:
        deployer.deploy(
            args.stack_name,
            template_path=args.template_path,
            env_template_path=args.env_template_path,
            wait=args.wait,
        )
    except DokployerError:
        logger = logging.getLogger(__name__)
        logger.exception("dokployer")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
