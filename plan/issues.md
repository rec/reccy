# Open project issues

Updated 2026-09-29. The numbers retain their original audit identities; gaps
are issues already resolved and removed from this list. These are static-review
findings, not all reproduced failures. Platform-specific behavior still needs
native verification.

## Deferred decisions

- **Public API naming and result semantics (27).** Service `install/start/stop`
   return `StatusResult.installed` without proving the daemon is running;
   `status()` performs that observation. `ProtocolClient.shutdown()` requests
   peer shutdown whereas `close()` disconnects locally. Renaming can wait
   because consumers need coordinated changes; backward compatibility is not
   required.

## RPC work and shutdown policy, deferred to the end

- **Handler and listener lifecycle (7, 9, 10, 11, 13).** A handler that blocks
  after accepting a request can occupy one of 16 slots indefinitely, and
  closing the server does not stop active handlers. The server is single-use,
  new requests are gated during startup/stopping, event order is serialized,
  and generic listeners close on EOF. Later decisions are whether handlers
  need application-owned deadlines or cooperative cancellation, whether
  shutdown waits for them, and whether slow event subscribers may delay
  publication or must be isolated. Generic listener threads also lack a
  join/completion contract, and `ProtocolClient.shutdown()` does not confirm
  handshake or write success. These choices affect when application resources
  may safely be released.

## Additional work beyond the prompt

None.
