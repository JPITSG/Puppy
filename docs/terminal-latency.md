# Terminal latency audit

The terminal streams PTY bytes over WebSockets into the vendored xterm.js 5.5.0
renderer. It does not use the managed browser's JPEG screencast path. The scan
covered `TermView`, xterm's input/write/render scheduling, the controller's
WebSocket relay, PTY input/output, reconnect replay, per-viewer queues, resize
handling, and the shared terminal agent's snapshot/wait path.

## Changes

- **Blocked input:** replace the 10 ms retry sleep with an asyncio descriptor
  writer callback. Keystrokes still send immediately; a backed-up paste resumes
  when the PTY accepts more bytes. The existing serialized input and five-second
  deadline remain. Partial writes use memoryview slices, avoiding repeated
  copies of the remaining paste. Closing or cancelling removes the writer
  callback and wakes a blocked writer safely before the descriptor can be reused.
- **Display output:** drain bytes already available from the PTY, up to 64 KiB
  per readiness callback, and send them together. On this host, individual PTY
  reads during a burst commonly returned about 4 KiB despite requesting 64 KiB.
  Combining ready reads reduces socket messages, queue operations, browser
  message callbacks, and agent output wake-ups. No timer waits to fill a batch;
  the byte budget gives input and other connections a turn during continuous
  output. Escape sequences and UTF-8 remain byte-for-byte intact.
- **Agent inspection:** previously, a snapshot rendered every retained line
  character by character before keeping its requested tail. That synchronous
  work could stall every socket on the node. Escape stripping still sees the
  full retained buffer (OSC strings can span lines), then only the requested
  lines are rendered. Ordinary text and CRLF lines use a string fast path.
  Carriage returns, backspaces, tabs, truncation, and output cursors retain
  their existing semantics.
- **Resizing:** xterm's actual grid-change event sends resize messages, instead
  of every container observer callback. Attach and showing a tab still announce
  its dimensions, including after another viewer resized the shared PTY.
  The node ignores unchanged dimensions and relies on `TIOCSWINSZ` to signal
  the foreground process group, avoiding a second signal to the shell.
- **Slow viewers:** retain the 512-message limit and add a 4 MiB byte limit to
  the pending application queue so larger batches cannot inflate it to 32 MiB.
  This allows a complete 2 MiB reconnect replay plus live output. The in-flight
  send and transport/browser buffers are separate. Overflow cancels the sender
  as well as closing its socket; a stalled viewer does not block healthy ones.

## Measurements

`python3 tests/terminal_latency_bench.py` runs a raw-mode PTY child and a local
aiohttp server/client. It measures 200 one-byte round trips, five 3,604,480-byte
output bursts, and three default agent snapshots after filling retained output.
It needs no engine, external service, or new dependency. Data stays under the
test copy's private `data/tests/` directory.

Initial alternating runs against the task's base and the changed reader showed:

| Measurement | Before | After |
| --- | --- | --- |
| Median one-byte round trip per run | 0.050–0.052 ms | 0.051–0.055 ms |
| Delivery of a 3.6 MB burst | 23.8–30.6 ms | 8.7–14.8 ms |
| WebSocket frames per burst | 880–881 | 90–147 |
| Conversion of a full 2 MiB text transcript | 341–367 ms | about 15 ms |

The ordinary keystroke path was already lean. The input improvement targets
backpressure and event-loop stalls; it is not a claim that idle typing became
noticeably faster. Burst timings measure receipt of bytes, not screen paint.
The real console test separately verifies keyboard input, parsing and rendering
the full burst, Unicode, subsequent input, and delivery of a real foreground
resize signal. Timings vary with load and hardware; tests assert correctness,
not a machine-specific millisecond threshold.

## Paths already lean and remaining limits

`TermView` immediately encodes and sends xterm input and writes binary
ArrayBuffers directly into xterm. The controller relay pumps both directions
independently without a batching timer or JSON/base64 conversion of PTY bytes.
The installed aiohttp transport already enables TCP_NODELAY. The vendored
xterm write buffer already prioritizes the first output after user input and
coalesces rendering with requestAnimationFrame. Additional frontend timers or
changes to those private renderer internals would undermine those paths.

Network round trips and the terminal application's own redraw cost remain.
The benchmark uses loopback without WebSocket compression; it does not measure
remote TLS links, phones, or negotiated browser compression. The real console
test exercises the actual browser connection but does not model a WAN.

A sustained producer can still outrun browser parsing: the protocol has no
xterm parse acknowledgements. The server queue limit isolates a slow transport,
but does not bound a browser's internal receive/parse queue. End-to-end flow
control would need negotiated acknowledgements and a policy for independent
viewers; terminal escape bytes cannot simply be dropped like stale video frames.
Pathological long lines containing repeated cursor controls still require
character processing in agent snapshots.

## Validation

- `python3 tests/terminal_test.py`: partial/blocked input, ordering, timeout,
  cancellation, closure, bounded draining, EOF, slow-viewer isolation, replay,
  transcript controls, lifecycle, and shared-agent behavior.
- `python3 tests/console_browser_test.py`: real xterm keyboard input, burst
  rendering, Unicode, fractional container resizing, and foreground SIGWINCH,
  alongside the existing console checks.
- `python3 tests/backend_test.py`: packaged headless runtime, authenticated
  terminal WebSocket, transport, and existing backend contracts.
- `python3 tests/timeouts_test.py` and `node tests/live_controls_ui_test.js`:
  unattended lifecycle and shared console controls.
- 10,000 deterministic randomized transcript cases compared against the base
  implementation produced identical results.

The wire protocol, persisted shapes, dependencies, and console appearance are
unchanged. Both runtimes use the same terminal implementation.
