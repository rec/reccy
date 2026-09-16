"""Stateful dictionary deltas, not JSON encoding or line framing.

For non-key fields, absent and None are equivalent in full input records.
Compression omits an initially null field and emits None when a previously
non-null field disappears. Decompression retains that None, rather than deleting
the key, so reconstruction preserves this equivalence, not exact dictionaries.
An absent field in an encoded delta means unchanged, not cleared.

Each instance retains independent state per string key across calls. Use fresh
compressor and decompressor instances for each independent stream, and consume
successive batches in order.
"""

from collections.abc import Iterable, Iterator


class Jsonl:
    def __init__(self, key: str) -> None:
        self.key = key
        self._previous_values: dict[str, dict[str, object]] = {}

    def __call__(self, it: Iterable[dict[str, object]]) -> Iterator[dict[str, object]]:
        for d in it:
            if not isinstance((key := d[self.key]), str):
                raise ValueError(f'Field "{self.key}" must be a string')

            prev = self._previous_values.setdefault(key, {})
            res = self._call(prev, d)
            prev |= res
            yield res

    def _call(self, prev: dict[str, object], d: dict[str, object]) -> dict[str, object]:
        raise NotImplementedError


class Compress(Jsonl):
    def _call(self, prev: dict[str, object], d: dict[str, object]) -> dict[str, object]:
        changed = {k: v for k, v in d.items() if k == self.key or prev.get(k) != v}
        missing = {k: None for k, v in prev.items() if k not in d and v is not None}
        return changed | missing


class Decompress(Jsonl):
    def _call(self, prev: dict[str, object], d: dict[str, object]) -> dict[str, object]:
        return prev | d
