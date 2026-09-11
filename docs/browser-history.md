# Browser Back and Forward

Puppy's browser history tracks destinations in the console. The browser's Back
button, Forward button, keyboard shortcuts and history menu use the same path.
There is no extra entry trapping the reader at the console's starting page.

## Navigation audit

| Surface or action | History behavior |
| --- | --- |
| Open or select a session, Settings, Search, terminal, browser or VNC tab | One destination; Back changes the selected view and keeps other tabs and running work alive |
| Focus another split pane, or move a tab into another pane | Records the selected destination; traversal uses the tab's current pane and preserves the edited layout |
| Main and task tabs, including tasks opened from the Tasks sheet | Records the selected conversation, including hidden task tabs reopened by history |
| Submit a search | Records query, filters, backend selection and result list together; editing filters or typing alone adds no stop |
| Open a search hit, session citation, or jump to latest | Records the conversation and message location; Back restores the prior reading position or search results |
| Settings backend selectors in Timeouts, Timers and System prompt | Records the backend being viewed; restores it through the existing read and draft-handling paths |
| New session/task/terminal/browser/VNC, Open session, Tasks, Review task, Agent notes, linked workspace, Move to directory, Edit backend, engine defaults/switch, VNC shortcut, draft conflict review, request/workflow detail, notices | Dialog entries; Back dismisses the top layer; Forward opens a fresh dialog using current data |
| Confirmations, one-field prompts and Remove task confirmation | Back cancels the pending choice; Forward cannot revive a resolved promise or repeat its action |
| Delayed operation/read progress, including nested progress over a form | Back invokes the existing dismissal/cancellation contract; a cancellation that owns its dialog keeps it visible until cleanup finishes |
| Phone drawer, Host activity and Notifications | Separate layers, including the drawer's swipe gestures; Back closes them, Forward reopens them. The two footer boxes share the space under the engine stats, so opening one closes the other |
| Close a tab | Records the resulting destination. Session, Search and Settings views can be reopened; closed terminal/browser/VNC resources are never recreated |
| Re-select a destination, streaming updates, polling, background agent-created tabs | No additional navigation stop |
| Scroll, load more results/messages, expand a tool/task card, unfold a queue, sidebar disclosure/filter/archive controls (including CPU, Latency and Processes inside Host activity), choice/context menus, browser/VNC typing controls, notices arriving in an open Notifications box | Local presentation and input controls; no additional stop. Menus dismiss when history changes |
| Type a draft, recall a prompt, change a model/permission, send/steer/ask/stop, save/reset a setting, apply/delete/sync, reorder/resize layout, toggle theme/alerts | Commands and preferences are not undone or replayed by history |
| Embedded browser's own Back/Forward buttons | Continue navigating that browser's website history; the enclosing console's history selects console views |

Closing a dialog with its own control consumes its history layer. Closing a
sheet and opening another view in the same action creates one destination.
Opening a nested confirmation and going Back leaves the underlying editor and
its unsaved fields in place. Leaving the editor itself runs its normal cleanup:
Forward opens a fresh form, and cancelled staged task attachments stay discarded.
Back never silently submits a form. Settings fields survive ordinary tab switches.

The console stores only validated view identifiers, message positions, backend
IDs and opaque page-local keys in `history.state`. URLs contain view identifiers
and message/backend positions. Query text, results, form text, passwords, tokens,
terminal commands and reopen functions are not added to browser history storage.
Search results, reading positions and dialog reopen functions are retained for
the most recent 128 entries in this page's memory. Reloading or visiting an older
evicted entry still restores available base views and message destinations; it
does not resurrect a form, search results or a pending operation. Existing
`#session=<node UUID>/<session ID>&seq=<event>` citation links remain supported.
A deleted session or closed viewer reports that its view is unavailable.

## Implementation contract

`puppy/static/navigation.js` owns `ConsoleHistory`: strict entry validation,
initial `replaceState`, batched pushes, asynchronous close traversal, `popstate`,
bounded memory and layer ordering. It is an authenticated console asset loaded
before `app.js`, with the same version substitution and cache policy.

`app.js` supplies the adapter: `navigationRoute`, `navigationCapture`,
`navigationApply`, URL handling, and the shared `navigationRemember()` /
`navigationChanged()` hooks. Remember the current view **before** changing its
destination, then mark the change; synchronous parts of one action coalesce.
Background invalidation uses `reconcile()` to replace the current destination
instead of adding an action the reader never took. Scroll enriches the current
entry without pushing. Transcript positions keep an event anchor so replacing
a loaded history window does not confuse an old pixel offset with new content.
Async links and transcript windows check their request/revision before landing.

`modal(html, className, reopen)` registers every dialog with the shared layer
stack. Its optional third argument must only open a fresh surface, re-reading
current identities/data as needed. Never use a callback that starts an operation,
submits a form, resolves an old confirmation, or creates a remote resource.
`onClose` continues to own all listeners, Composer/upload cleanup and promise
settlement; `onDismiss` continues to own cancellation. Do not add a separate
`popstate` listener to a feature. A non-modal navigable panel uses
`navigation.layer(dismiss, reopen)` and calls its returned release function when
it closes. Keep permission, progress and destructive-action semantics intact.

Every new or changed destination must update this audit and its regression
coverage. Run `node tests/navigation_ui_test.js` for the controller and
`python3 tests/console_browser_test.py --navigation-only` for the real console.
The full console browser suite also exercises Back during cancellable operations.
The authentication surface suite covers anonymous refusal of the history asset.
