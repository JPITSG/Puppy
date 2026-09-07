# Shared remaining usage

The footer uses one latest weekly percentage for every entry with the same
provider, user, account/workspace, exact quota bucket and window duration.
Changing machines or models does not create another allowance. Models with
separate allowances must not contribute to the all-model weekly figure.
Only percentages and their observation/reset information are shared. Engine
readiness, sign-in state, installed versions and upgrades remain node-owned.
The tooltip names the backend that supplied the selected observation.

## Identity and bucket proof

The additive `account-quota-v1` capability carries an engine's `usage_monitor`
object through the existing authenticated HTTP and state-stream payloads.
The monitor's account key is SHA-256 over namespaced, stable identifiers;
raw account IDs, email addresses and credentials are never published.

- Codex: the selected ChatGPT account ID in the CLI's `auth.json` must match
  its ID-token account claim; the ChatGPT user ID is also required. API-key,
  external/keyring-only and custom endpoint configurations without that proof
  cannot share. Token renewal does not change the key. This reads identity
  metadata from the CLI's own store, not a new authentication mechanism.
  Only an explicit `codex` limit ID (or the `codex` entry in the account
  response's named-limit map) supplies the displayed week. Live notifications
  follow the same bucket check as independent account reads.
- Claude: the CLI's non-secret `oauthAccount` account and organization UUIDs
  identify the owner. Its auth-status reply must confirm the matching
  first-party Claude subscription, organization and email. A turn separately
  proves its subscription account through `initialize`'s `account` metadata;
  API helpers, external tokens and third-party providers cannot borrow the
  stored subscription's identity. The displayed bucket is exactly
  `unifiedWindows.seven_day`, with exact `rateLimitType: seven_day` as the
  older event fallback. Five-hour and model-specific windows never replace it.
- OpenCode currently exposes no account-quota identity or weekly observations
  through its driver. Selecting a similarly named model does not opt it in.

The local Claude metadata paths and native account fields were checked against
the installed CLI source; Codex's local login shape was checked without printing
credentials or account values. Unsupported identity shapes fail closed.

## Freshness and compatibility

Each driver captures identity before a turn/read and checks it again when an
observation arrives. A login change clears that node's in-memory monitor; an
old process cannot label its result with the new account. No old database or
rollout reading is retroactively bound to the current login. A restart requires
a fresh observation or a verified matching connected peer.

Selection uses observation time, never packet arrival order or the largest
percentage. Account reads are dated at request start so a slow read cannot
overwrite a live observation received while it was pending. Equal timestamps
select the larger used percentage deterministically. Known expired windows
are hidden; missing reset times stay local. Future timestamps more than a
minute ahead are rejected. As with other cross-node wall-clock observations,
backends should keep their clocks synchronized.

Offline backends, unhealthy logins and nodes without `account-quota-v1` do not
contribute new shared observations. The console retains already received
observations until their reset (up to 1,024 account/bucket entries, oldest
first), so a contributor disconnecting or changing accounts cannot roll another
matching row back to an older percentage. Older nodes show their own legacy readings.
Nodes with an unidentified account can show fresh local observations but cannot
contribute them to other accounts. An unidentified bucket does not supply a
shared weekly percentage.

Polling intervals and the manual refresh control remain per backend. This
feature adds no model calls, database/config format changes or durable cache;
snapshot export/import is unchanged. Account keys and observations are rebuilt
from the current login and fresh engine data after restart.

Tests: `python3 tests/quota_test.py`, `node tests/quota_ui_test.js`, and
`python3 tests/usage_refresh_test.py` exercise synthetic identities, strict
bucket matching, login changes, delayed reads and the authenticated payload.
