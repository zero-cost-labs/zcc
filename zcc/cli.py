"""ZCC command-line interface."""

from __future__ import annotations

import logging
import sys

import click

from . import __version__
from .loader import ConfigError, load_cluster
from .deploy.orchestrator import DeployOrchestrator


@click.group()
@click.version_option(__version__, prog_name="zcc")
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose logging.",
)
def cli(verbose: bool) -> None:
    """ZCC — Zero Cost Cluster management tool powered by k0s."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s  %(message)s")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("config", type=click.Path(exists=True))
def validate(config: str) -> None:
    """Validate a cluster YAML configuration file."""
    try:
        cluster = load_cluster(config)
    except ConfigError as exc:
        click.secho(f"✗  {exc}", fg="red", err=True)
        sys.exit(1)

    click.secho(f"✓  Cluster '{cluster.name}' is valid", fg="green")
    click.echo(f"   Version  : {cluster.version}")
    click.echo(f"   Hosts    : {len(cluster.hosts)}")
    click.echo(f"   Features : {len(cluster.features)}")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("config", type=click.Path(exists=True))
def plan(config: str) -> None:
    """Show the deployment plan for a cluster without executing it."""
    try:
        cluster = load_cluster(config)
    except ConfigError as exc:
        click.secho(f"✗  {exc}", fg="red", err=True)
        sys.exit(1)

    orchestrator = DeployOrchestrator(cluster)
    for line in orchestrator.plan():
        click.echo(line)


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("config", type=click.Path(exists=True))
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the deployment plan without making any changes.",
)
def deploy(config: str, dry_run: bool) -> None:
    """Deploy a cluster from a configuration file."""
    try:
        cluster = load_cluster(config)
    except ConfigError as exc:
        click.secho(f"✗  {exc}", fg="red", err=True)
        sys.exit(1)

    orchestrator = DeployOrchestrator(cluster)

    if dry_run:
        click.echo("Dry-run — no changes will be made.\n")
        for line in orchestrator.plan():
            click.echo(line)
        return

    try:
        orchestrator.deploy()
    except Exception as exc:  # noqa: BLE001
        click.secho(f"✗  Deployment failed: {exc}", fg="red", err=True)
        sys.exit(1)

    click.secho(f"✓  Cluster '{cluster.name}' deployed successfully", fg="green")
