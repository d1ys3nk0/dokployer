"""CLI for dokployer."""

from __future__ import annotations

import argparse
from pathlib import Path

from dokployer.dokploy_client import DokployClient
from dokployer.template_manager import TemplateManager


def cli(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run the deployment."""
    parser = argparse.ArgumentParser(
        prog="dokployer",
        description="Upload an interpolated Docker Swarm stack to Dokploy.",
    )
    parser.add_argument("stack_name", help="Dokploy compose stack name")
    parser.add_argument(
        "-f",
        "--compose-template",
        type=Path,
        help="Path to the stack YAML template. Reads stdin when omitted.",
    )
    parser.add_argument(
        "--env",
        dest="env_file",
        type=Path,
        help="Optional Dokploy env file template to upload with the stack.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll Dokploy until the deploy finishes or times out.",
    )
    args = parser.parse_args(argv)

    template_manager = TemplateManager()
    dokploy_client = DokployClient(template_manager)
    dokploy_client.deploy_stack(
        args.stack_name,
        args.compose_template,
        env_file=args.env_file,
        wait=args.wait,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
