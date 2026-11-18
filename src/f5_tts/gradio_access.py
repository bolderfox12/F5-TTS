"""Keep Gradio on loopback unless the operator configures authentication."""

import os

import click


def gradio_access(host, share):
    host = host or "127.0.0.1"
    username = os.environ.get("F5_GRADIO_USERNAME")
    password = os.environ.get("F5_GRADIO_PASSWORD")
    if bool(username) != bool(password):
        raise click.UsageError("Set both F5_GRADIO_USERNAME and F5_GRADIO_PASSWORD.")
    if (share or host.lower() not in {"127.0.0.1", "localhost", "::1"}) and not username:
        raise click.UsageError("Remote Gradio access requires F5_GRADIO_USERNAME and F5_GRADIO_PASSWORD.")
    return host, (username, password) if username else None
