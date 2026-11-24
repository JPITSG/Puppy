# Drives that stop answering

A project may live on a network drive, a removable disk or a FUSE mount. One of
those that stops answering does not fail: whoever touches it waits in the kernel
for as long as that lasts, often past any signal. Puppy runs on one event loop,
so a single such touch there would stop it for everybody, whatever session it
was for; a touch in a thread pool would take the thread, and would keep the
process from ending, so that Puppy could not even be restarted.

So nothing outside Puppy's own storage is touched on the event loop, and nothing
in Puppy's process goes to a drive before the drive has answered. This holds in
both runtimes.

## What you see

- A drive that has not answered for ten seconds counts as gone. Its sessions
  turn red and are paused exactly like sessions whose folder is missing: the
  draft stays, nothing starts there, and waiting queues hold as they stand. What
  was last known of the folder - its agent notes, for one - stays listed.
- Everything else goes on: the session list and every conversation open at
  once, other sessions' turns, tasks, terminals, searches and backups run.
- What needs the drive says so instead of waiting: *`/mnt/nas/project` is not
  answering; its disk or network drive may be disconnected*. The first to ask is
  told after ten seconds, everybody after that at once. Nothing that was refused
  is done later when the drive comes back.
- When it answers again, its sessions return by themselves, usually within a
  second: queues that waited move on and the Git mark is looked at again. A
  drive that was unmounted and mounted again is a new one, whatever still waits
  on the old.
- Puppy starts, stops and can be restarted while a drive does not answer.

| What | While its drive does not answer |
| --- | --- |
| Session list, conversations, search, agents' session tools | As ever; they never look at a project |
| A message, loop, session tool or held re-send there | Refused, the session is paused. A turn that was just starting ends with the reason |
| New session there, folder picker, new folder | Refused with the reason (`409`) |
| Agent notes, Git history, push and revert | Refused with the reason (`409`) |
| Git mark and sheet | The repository could not be checked, with the reason |
| Terminal, spawned agent | Refused with the reason (`409`) |
| New task, refresh from Main, apply to Main | Refused with the reason (`409`). A task's own review still opens |
| Apply to Main when done | Waits for Main's working folder; other Mains' tasks apply meanwhile |
| A scheduled run there | Recorded as failed with the reason |
| A picture a tool named | `503`, which is no proof that the file is gone, so its thumbnail stays |
| A file an agent shares | The tool call fails with the reason |
| Moving a project off or onto it | Refused with the reason (`409`) |
| A linked workspace there | Its synchronization fails with the reason (`409`) |
| Backups onto it | The attempt is recorded as failed with the reason; saved copies there read **Unavailable** |
| Host activity | Its Disk row shows "no answer" |

## How it works

`puppy/places.py` is the one way to a file or folder outside Puppy's own storage.
`await places.ask(path, function, *args)` has two parts.

**It arrives.** The path is followed one name at a time, links included, so that
the mount being touched is always known, and a mount that is not one Puppy
itself stands on is asked something its server must answer (`statvfs`; names and
attributes may all come from a cache). A process of its own does this, the
*scout* (`puppy/place_scout.py`): a small Python process that uses nothing but
the standard library, is started when it is first needed and ends with Puppy.
Puppy can leave a scout that is stuck behind. If it dies it is replaced; if none
can be started, a thread goes instead.

A caller that has not arrived after `TOUCH_SECONDS` (10) gets `NotAnswering`,
and the mount the scout stands on is *silent* from then on: nobody else is sent
there until the scout is back from it. At most eight callers are on their way to
one mount at a time, and the line behind them waits on the event loop, where it
costs nothing. Mounts are told apart by the kernel's mount ID from
`/proc/self/mountinfo`, which is read in a thread.

**It works.** Once the place has answered, the function runs in a thread of its
own for as long as `limit` lets its caller wait (30 seconds unless it says
otherwise). Work that lasts is asked after every ten seconds: when the place no
longer answers, its caller is told rather than left waiting. Work that takes as
long as it takes - clones, copies, workspace sync - runs in the `jobs` pool
behind `places.visit(path)`, which is the arrival alone.

A thread that has arrived stays on the drive it arrived at. A link inside a
project that leads to another drive - a note, a worktree's repository - is not
followed there before that drive has answered too, so it makes neither the
folder unavailable nor anybody wait.

Mounts that Puppy stands on - those holding its data, its program, its Python
and the root - are never silent: if one of them stopped answering, Puppy could
not say so. Calls there are bounded and off the loop all the same.

Programs are started without going there either. A program is started by a child
that enters its folder before it becomes the program, and the event loop waits
until it has; the child also holds Puppy's listening socket until then. On a
drive, an engine or a terminal is therefore started through `/bin/sh`, which
changes the folder itself and becomes the program, and Git is run as
`git -C <folder>`.

Session lists are built from what `puppy/session_directories.py` last saw:
whether each folder is there, which agent notes it holds and where it really is
with every link followed. Each folder is looked at by a check of its own, so one
that hangs keeps no other folder's answer back.

## Puppy's end

Task copies, applies and moves that were accepted are waited for when Puppy
stops, as long as their drives answer; every twenty seconds it asks. Running
engines are stopped together, and other requests under way get twenty seconds.
Whatever is left fifteen seconds after the last part of an orderly stop is
written to the log with every thread's stack and left behind, because Python
itself would wait for its worker threads for ever.

## Limits

- Work already under way inside a drive at the moment it dies keeps its thread
  until the drive answers: a copy into a project, a workspace sync, a backup
  being written to it. It holds its own project, session or backup and, if
  sixteen of them hang at once, the `jobs` pool. Drives whose requests cannot be
  interrupted, such as a FUSE mount whose daemon holds them, also keep Puppy's
  process from ending meanwhile; unmounting the drive by force, or ending its
  daemon, releases both.
- An engine, a terminal's process or a Git command that is stuck in a drive
  cannot be ended before the drive answers. Its session stays *running*; Stop
  and Close answer, and everything else goes on.
- Finding out which drive a path is on needs Linux's mount list. Elsewhere every
  call is still bounded and off the loop, but no drive is remembered as silent.
- The account's home folder, with the engines' sign-ins, is expected to answer.

Nothing here is persisted or configurable, and there is no capability.
`tests/places_test.py` holds the rules without any drive, and
`tests/dead_mount_test.py` first catches every touch of a project made by the
event loop's own thread and then, as root with `/dev/fuse`, uses a drive that
really hangs.
