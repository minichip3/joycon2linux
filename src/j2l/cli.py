"""CLI entry point (click)."""
import click


@click.group()
def cli():
    """Joy-Con 2 Linux bridge — connect Switch 2 controllers via BLE GATT."""
    pass


@cli.command()
def scan():
    """Scan for nearby Switch 2 controllers."""
    # TODO: BLE scan via bleak
    click.echo("Not implemented yet.")


@cli.command()
@click.argument('mac')
def pair(mac):
    """Pair a controller by MAC address."""
    # TODO: BlueZ pairing
    click.echo(f"Pair {mac} — not implemented yet.")


@cli.command()
@click.argument('mac')
def unpair(mac):
    """Unpair a controller."""
    # TODO: BlueZ unpairing
    click.echo(f"Unpair {mac} — not implemented yet.")


@cli.command()
@click.option('--combined', is_flag=True, help='Combine left+right Joy-Con')
@click.option('--side', type=click.Choice(['left', 'right']), help='Single Joy-Con side')
@click.option('--dsu', is_flag=True, help='Enable DSU/cemuhook UDP server')
def run(combined, side, dsu):
    """Auto-discover and connect controllers."""
    # TODO: Main bridge loop
    click.echo("Not implemented yet.")


@cli.command(name="list")
def list_paired():
    """List paired controllers."""
    # TODO: Read config.yaml
    click.echo("Not implemented yet.")
