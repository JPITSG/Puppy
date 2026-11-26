# Worker threads

Puppy runs on one event loop, and whatever would block it runs in a thread.
Which thread matters: the loop's own default pool is also aiohttp's, so every
script and style sheet the console loads, every compressed answer and every
large WebSocket frame - a conversation's first snapshot, for one - goes through
it. Work that fills that pool does not only wait itself; it keeps people from
opening Puppy at all. So each kind of work has threads of its own, in both
runtimes, and the default pool is kept for short work.

## The pools

`puppy/workers.py` owns every thread pool. A call names the one it runs in:
`await workers.history.run(report, since, until)`.

| Pool | Threads | What runs there |
| --- | --- | --- |
| `io` | 32 | The loop's default pool: aiohttp's own file serving, compression and name lookups, and short reads and writes of Puppy's own storage |
| `jobs` | 16 | Work that takes as long as it takes: task copies and their Git, project and scratch moves, backups and restores, workspace sync, shared files, storage removal, remote screen pictures |
| `history` | 4 | Reading conversations and the token ledger out of the database |
| `host` | 4 | CPU, memory and processes |
| `search` | 4 | Full-history searches |
| `index` | 1 | The search index's one writer |

Sizes are fixed. Python's own default pool is the CPU count plus four - five
threads on a one-CPU backend - while `io` is 32 everywhere. A pool makes its
threads as it needs them, and one that is full delays only its own kind of work.

`operations.to_thread` - the long console operations' door - runs in `jobs`
and takes the caller's context along, so a step can report to its operation
and be cancelled by it. `Pool.run` does not, which cleanup relies on.

Files and folders outside Puppy's own storage belong to no pool: they may be on a
drive that stops answering, and a thread that waits for one cannot be taken
back. Each such touch is a call of `places.ask`, which sends a process of its own
ahead and then works in a thread of its own - the folder picker, agent notes,
whether the sessions' folders are there, a look at a repository (eight of those
at a time), a picture a tool named. Long work in such a folder runs in `jobs`
once the folder has shown that it answers. See
[drives that stop answering](silent-drives.md).

Waiting for a process to end takes no thread at all: `workers.child_exit` looks
every little while. A terminal's process that closes its terminal and lives on
therefore costs nothing, and Close still ends it - or, if it waits in the kernel
past any signal, leaves it to end by itself.

### What may run in `io`

Work on a bounded piece of Puppy's own storage, or on bytes already in memory.
Nothing that runs a program, follows a path somebody named, waits for a lock,
or takes time that grows with a project or a history.

`tests/workers_test.py` reads the sources and fails when anything outside
`puppy/workers.py` uses an executor or makes a thread of its own, when a
process is waited for without `WNOHANG`, or when the list of what runs in `io`
changes. Adding to that list is a claim about the work, made in the test with
its reason.

## Lanes

A pool bounds threads; a `Lane` bounds callers. It admits a few jobs at a time,
in order, optionally a smaller number for each caller, and everyone else waits
on the event loop. That costs nothing, and a caller that gives up leaves the
line at once - a thread that has started waiting can never be taken back. A
place belongs to the work rather than to whoever asked for it: it is free when
the thread has really ended. A full line refuses at once.

| Lane | At a time | For each caller | Waits at most |
| --- | --- | --- | --- |
| Searches from the console | 2 | - | 8 s |
| Agents' searches | 2 | 1 | 20 s, or 2.5 s for one relayed from another backend |
| Agents' reads of conversations (the session and task tools') | 2 | - | 20 s, or 2.5 s for one relayed from another backend |

An agent's caller is its session: the parallel searches of one turn, its
sub-agents' included, run one after the other, and leave the other place to
another session. The console's searches never wait for an agent's.

## Searches

A search's cost is decided by whoever words it, and agents run many at once.

- **The full-text match drives the query.** The statements use `CROSS JOIN`,
  SQLite's promise to keep that order. With only a session filter and no
  statistics SQLite otherwise takes a session for ten rows and repeats the
  whole match for every row of it.
- **One pass serves a request.** Every match is ranked once into the reading
  connection's own memory; totals, each session's page and the groups are read
  from there, and excerpts are made for the rows that are shown. Searching
  fifty sessions costs what searching one does.
- **Short words are whole words.** A bare word finds the words that begin with
  it, except one- and two-letter words (Latin letters and digits), which match
  whole: as a prefix, a single letter is a large part of every word there is.
  Scripts written without spaces keep their prefixes.
- **Searches have connections of their own**, read-only, beside the index's
  writer and each other. No search waits for a lock, and indexing waits for no
  search. Each sees one moment of the index throughout.
- **Every search has a limit of 8 seconds**, and stops sooner when nobody waits
  for it: the browser that asked has gone, the turn that asked has ended, or
  Puppy is shutting down. SQLite asks between steps whether to go on, so it
  stops within about a tenth of a second.

A search that is stopped or refused says so. `GET /api/search` answers `503`
with the reason and `Retry-After` - the Search tab shows it beside that
backend's name, and says *No matches* only when a backend has searched - and the
session tools name the sessions that were not searched and tell the agent to
narrow the search instead of repeating it.

Measured on a 1.3 GB index with 666,000 documents: eighteen searches that had
been estimated at six hours together take under two seconds, the widest words
over every session about one second, and the pages answer within a few
hundredths of a second meanwhile.

## The watchdog

Each running node has one thread that watches the rest. Every five seconds it
asks the event loop and the default pool for a sign of life. When one has not
answered for ten seconds it writes to the log (`data/puppy.log`, and the
service's output):

- what each pool is doing: threads busy, the longest-running piece of work by
  function name, how much waits and for how long;
- every thread's stack, threads in the same place together.

Neither holds arguments or other values. It repeats this every five minutes
while the stall lasts, at most six times, and logs one line when it is over. It
also logs once when work in any pool has waited a minute for a thread.

The instance keeps a notice and shows it on open consoles: *Puppy's worker
threads are all busy · pages may load slowly until one is free · details in the
log* when the pool stalls, and *Puppy did not respond for 23 s · details in the
log* once a standing loop moves again. A headless backend logs only.

## Scope

Nothing here is persisted or configurable, and no stored shape, index format,
capability or wire contract changes. Pools and lanes are per process; a
restart starts with empty ones. The watchdog reports and never acts: it neither
stops work nor restarts Puppy.

A thread that is inside the operating system cannot be interrupted; the pools
confine it to its own kind of work. Nothing in a pool is the first to find out
that a drive has stopped answering; only work in `jobs` that was already inside
the drive is kept by it. Puppy's own storage is read and written on the event loop
and in `io` as well, and is expected to answer.

When Puppy stops, whatever is still under way fifteen seconds after its last part
has ended is logged and left behind.
