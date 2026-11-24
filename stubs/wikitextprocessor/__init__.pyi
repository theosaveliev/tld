"""Type surface used from wikitextprocessor 0.4.96."""

from pathlib import Path


class Wtp:
    """Wiki-text processing context factory."""

    def __init__(
        self,
        num_threads: int | None = ...,
        cache_file: str | Path | None = ...,
        quiet: bool = ...,
    ) -> None: ...
