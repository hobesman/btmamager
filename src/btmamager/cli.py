"""Command line: `web` (default in Docker) runs the UI plus the background loop; `run-once` does a single pass."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import typer

from .core.config import get_store
from .core.manager import Manager

app = typer.Typer(add_completion=False, help="Keep qBittorrent seeding only the torrents you want from one tracker.")


@app.command()
def web():
    """Start the web UI and the background loop."""
    from .web.app import run as run_web
    run_web()


@app.command("run-once")
def run_once(dry_run: Optional[bool] = typer.Option(None, "--dry-run/--apply",
                                                    help="Override the saved dry-run setting")):
    """Run the policy once and print what happened."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    res = asyncio.run(Manager(get_store()).run_once(dry_run=dry_run))
    if not res.ok:
        typer.echo(res.error, err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
