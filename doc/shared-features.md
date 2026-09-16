# Shared infrastructure features

Consumer migrations are deferred. Implementations and tests here change only
Reccy; no claim is made that sibling projects have adopted these APIs.

## Atomic output

Import `atomic_output` from `reccy.runtime.files`. It creates parent directories
and yields a closed, initially empty temporary file beside the destination,
preserving its suffix. Write through any encoder, closing its handles before
leaving the context. A successful exit replaces the destination; failure leaves
the old destination intact and removes the temporary file.

```python
with atomic_output(destination) as temporary:
    temporary.write_bytes(contents)
```

Concurrent writes are last-replacement-wins. `sync=True` fsyncs the finished file
before replacement; it does not fsync the directory or promise full power-loss
durability. Existing text/JSON settings writers still default to syncing.
This is neither a resource lock nor a multi-file transaction. Callers own content
validation and any special permission policy.

Tuney migration: replace the local `app.file_output.atomic_output` implementation
and its imports with this function. Streamo migration: use it inside image/cursor
publication, retaining image validation. Neither migration is implemented here.
