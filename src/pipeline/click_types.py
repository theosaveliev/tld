from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from click import ParamType

__all__ = ["IN_PATH"]

IN_PATH: ParamType[Path] = click.Path(
    exists=True,
    file_okay=True,
    dir_okay=False,
    readable=True,
    allow_dash=True,
    path_type=Path,
)
