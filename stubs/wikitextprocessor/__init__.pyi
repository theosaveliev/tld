from pathlib import Path

class Wtp:
    def __init__(
        self,
        num_threads: int | None = ...,
        cache_file: str | Path | None = ...,
        quiet: bool = ...,
    ) -> None: ...
