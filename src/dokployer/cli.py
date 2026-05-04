"""CLI for dokployer."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dokployer.dokploy_client import DokployClient
from dokployer.errors import DokployerError
from dokployer.inspector import DokployInspector
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


def _add_global_args(parser: argparse.ArgumentParser) -> None:
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


def _add_deploy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "app_name",
        nargs="?",
        help="Dokploy compose app name",
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


def _command_index(argv: list[str], command: str) -> int | None:
    for index, arg in enumerate(argv):
        if arg == command:
            return index
        if arg == "--":
            return None
    return None


def _print_json(data: object) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def _string_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _record_fields(records: list[dict[str, object]]) -> list[str]:
    preferred = [
        "name",
        "state",
        "containerId",
        "currentState",
        "status",
        "deploymentId",
        "createdAt",
        "logPath",
        "composeId",
        "appName",
        "composeStatus",
        "error",
    ]
    keys = {key for record in records for key in record}
    ordered = [key for key in preferred if key in keys]
    ordered.extend(sorted(keys.difference(ordered)))
    return ordered


def _print_text(data: object) -> None:
    if isinstance(data, dict):
        for key in sorted(data):
            sys.stdout.write(f"{key}\t{_string_value(data[key])}\n")
        return

    if isinstance(data, list):
        records = [item for item in data if isinstance(item, dict)]
        if len(records) == len(data):
            if not records:
                return
            fields = _record_fields(records)
            sys.stdout.write("\t".join(fields))
            sys.stdout.write("\n")
            for record in records:
                sys.stdout.write("\t".join(_string_value(record.get(field)) for field in fields))
                sys.stdout.write("\n")
            return

        for item in data:
            sys.stdout.write(f"{_string_value(item)}\n")
        return

    sys.stdout.write(f"{_string_value(data)}\n")


def _run_deploy(args: argparse.Namespace) -> None:
    template = ComposeTemplate()
    client = DokployClient()
    deployer = StackDeployer(client, template)
    deployer.deploy(
        args.app_name,
        template_path=args.template_path,
        env_template_path=args.env_template_path,
        wait=args.wait,
    )


def _run_inspect(args: argparse.Namespace) -> None:
    client = DokployClient()
    inspector = DokployInspector(client)
    data: object
    if args.inspect_command == "app":
        data = inspector.app(args.app_name)
    elif args.inspect_command == "services":
        data = inspector.services(args.app_name)
    elif args.inspect_command == "containers":
        data = inspector.containers(args.app_name, running=args.running)
    elif args.inspect_command == "deployments":
        data = inspector.deployments(args.limit, args.app_name)
    else:
        data = None

    if args.json_output:
        _print_json(data)
    else:
        _print_text(data)


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print JSON instead of tab-separated text.",
    )


def _parse_inspect_args(raw_argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dokployer",
        description="Inspect Dokploy compose apps using the Dokploy API.",
    )
    _add_global_args(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_subparsers = inspect_parser.add_subparsers(
        dest="inspect_command",
        required=True,
    )
    app_parser = inspect_subparsers.add_parser("app")
    app_parser.add_argument("app_name", nargs="?")
    _add_json_arg(app_parser)
    services_parser = inspect_subparsers.add_parser("services")
    services_parser.add_argument("app_name", nargs="?")
    _add_json_arg(services_parser)
    containers_parser = inspect_subparsers.add_parser("containers")
    containers_parser.add_argument("app_name", nargs="?")
    containers_parser.add_argument("--running", action="store_true")
    _add_json_arg(containers_parser)
    deployments_parser = inspect_subparsers.add_parser("deployments")
    deployments_parser.add_argument("app_name", nargs="?")
    deployments_parser.add_argument("--limit", type=int, default=10)
    _add_json_arg(deployments_parser)
    return parser.parse_args(raw_argv)


def _parse_deploy_command_args(raw_argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dokployer",
        description="Upload an interpolated Docker Swarm stack to Dokploy.",
    )
    _add_global_args(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    deploy_parser = subparsers.add_parser("deploy")
    _add_deploy_args(deploy_parser)
    return parser.parse_args(raw_argv)


def _parse_legacy_deploy_args(raw_argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dokployer",
        description="Upload an interpolated Docker Swarm stack to Dokploy.",
    )
    _add_global_args(parser)
    _add_deploy_args(parser)
    return parser.parse_args(raw_argv)


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run the deployment."""
    raw_argv = sys.argv[1:] if argv is None else argv
    deploy_index = _command_index(raw_argv, "deploy")
    inspect_index = _command_index(raw_argv, "inspect")
    logs_index = _command_index(raw_argv, "logs")

    if inspect_index is not None:
        args = _parse_inspect_args(raw_argv)
        runner = _run_inspect
    elif deploy_index is not None:
        args = _parse_deploy_command_args(raw_argv)
        runner = _run_deploy
    elif logs_index is not None:
        sys.stderr.write("dokployer: error: logs command was removed; use Dokploy API data only\n")
        return 2
    else:
        args = _parse_legacy_deploy_args(raw_argv)
        runner = _run_deploy

    _configure_logging(verbose=args.verbose, quiet=args.quiet)

    try:
        runner(args)
    except DokployerError as exc:
        logger = logging.getLogger(__name__)
        if args.verbose:
            logger.exception("dokployer")
        else:
            logger.error("%s", exc)  # noqa: TRY400
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
