"""CLI entry point (click)."""

from __future__ import annotations

import logging

import click


@click.group()
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging.",
)
def cli(verbose: bool) -> None:
    """Joy-Con 2 Linux bridge — connect Switch 2 controllers via BLE GATT."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


@cli.command()
@click.option("--timeout", "-t", default=10, type=int, help="Scan duration in seconds.")
def scan(timeout: int) -> None:
    """Scan for nearby Switch 2 controllers."""
    import asyncio

    import j2l.scanner

    results = asyncio.run(j2l.scanner.scan(timeout=timeout))
    if not results:
        click.echo("No controllers found.")
        return
    for mac, info in results.items():
        click.echo(f"{mac}  {info.name}  ({info.type})  RSSI: {info.rssi}")


@cli.command()
@click.argument("mac")
@click.option("--name", "-n", default=None, help="Friendly name for the controller.")
@click.option(
    "--type",
    "-t",
    "type",
    default="pro_controller2",
    type=click.Choice(["joycon2_left", "joycon2_right", "pro_controller2"]),
    help="Controller type.",
)
def pair(mac: str, name: str | None, type: str) -> None:
    """Pair a controller and save it to the config."""
    from j2l.config import Config

    cfg = Config()
    cfg.add_controller(mac, controller_type=type, name=name)
    click.echo(f"Paired {mac} as {type}")


@cli.command()
@click.argument("mac")
def unpair(mac: str) -> None:
    """Remove a controller from the config."""
    from j2l.config import Config

    cfg = Config()
    if mac in cfg.list_controllers():
        cfg.remove_controller(mac)
        click.echo(f"Unpaired {mac}")
    else:
        click.echo(f"{mac} not found in config")


@cli.command(name="list")
def list_paired() -> None:
    """Show paired controllers."""
    from j2l.config import Config

    cfg = Config()
    controllers = cfg.list_controllers()
    if not controllers:
        click.echo("No paired controllers.")
        return
    for mac, info in controllers.items():
        click.echo(f"{mac}  {info.get('name', 'Unknown')}  ({info.get('type', '?')})")


@cli.command()
@click.argument("mac")
@click.option("--name", "-n", default=None, help="Name reported by the uinput device.")
@click.option(
    "--type",
    "-t",
    "type",
    default="pro_controller2",
    type=click.Choice(["joycon2_left", "joycon2_right", "pro_controller2"]),
    help="Controller type.",
)
@click.option(
    "--combined/--no-combined",
    default=True,
    help="Expose a combined Pro Controller layout instead of a half Joy-Con.",
)
def run(mac: str, name: str | None, type: str, combined: bool) -> None:
    """Connect to a controller and run the bridge."""
    import asyncio

    from j2l.bridge import run_bridge

    asyncio.run(run_bridge(mac, type, gamepad_name=name, combined=combined))


@cli.command()
@click.option(
    "--all",
    "connect_all",
    is_flag=True,
    default=False,
    help="Connect all paired controllers instead of just the first one.",
)
def connect(connect_all: bool) -> None:
    """Connect paired controller(s) from the saved config."""
    import asyncio

    from j2l.bridge import run_bridge
    from j2l.config import Config

    cfg = Config()
    controllers = cfg.list_controllers()

    if not controllers:
        click.echo("No paired controllers. Use 'pair' first.")
        return

    if connect_all:
        async def _connect_all() -> None:
            tasks = [
                asyncio.create_task(
                    run_bridge(
                        mac,
                        info["type"],
                        gamepad_name=info.get("gamepad_name"),
                        combined=info.get("combined", True),
                    )
                )
                for mac, info in controllers.items()
            ]
            await asyncio.gather(*tasks)

        asyncio.run(_connect_all())
    else:
        mac, info = next(iter(controllers.items()))
        asyncio.run(
            run_bridge(
                mac,
                info["type"],
                gamepad_name=info.get("gamepad_name"),
                combined=info.get("combined", True),
            )
        )


if __name__ == "__main__":
    cli()
