# NordicBytes Green upload credit

MediaSeed keeps a persistent, local estimate of tracker-creditable upload for
each torrent when a tracker account is configured. Publication dates and bonus
flags require an exact info-hash match; a similar title is never sufficient.

NordicBytes Green upload is counted at 0.5x during the first 24 hours from the
tracker's publication timestamp. As in FjordSeed, enforcement includes a further
30-minute margin for announce/clock skew. The interface shows both times. Own
uploads are exempt only when the API uploader matches the configured account's
confirmed username. Missing/invalid publication data keeps accounting pending
and conservatively discounts upload instead of assuming full credit.

The SQLite `torrent_credit` table stores the selected goal, cumulative discounted
bytes, pending observations, publication evidence and sampling baseline. Upload
observed across expiry is discounted for that whole interval. Discounted bytes
retain their half-credit after expiry and restarts; newly observed upload after
expiry counts normally. Historic upload that cannot be timed is explicitly
marked as estimated. Counter resets never create negative or invented credit.
Confirmed publication evidence survives expiry/pruning of the cover cache.

Without automatic cleanup, seeding has no native time or ratio limit. The
selected ratio remains an informational target for manual early-stop/removal
confirmation and tracker accounting. Green credit never stops a retained torrent.
The worker idempotently clears legacy native limits, including installations
without tracker accounts, without resuming paused torrents. A requested start
clears all native limits first. Newly started imports are configured while
paused; pending metadata keeps the selected start durable until preparation
succeeds. The existing MediaVPN gate verifies every mutation independently.

Manual torrent files, magnets, browser-extension imports, tracker imports and
RSS share the same adapter. Automatic cleanup uses credited ratio and does not
have its explicit time/ratio/both/either policy replaced by the native stop
policy. Manual early stop/removal checks credit rather than raw ratio. Explicit
confirmation remains available. Existing rules for connected downloaders and
explicit file deletion remain in force.

The torrent list and Details show raw versus credited upload/ratio, the period,
pending/estimated states, and fresh verified freeleech, double upload, featured,
internal and refundable flags. Bonus flags are informative: MediaSeed does not
invent account credit from them. Actual tracker-wide account totals come from
the separate user-statistics API cards. This feature does not claim to reproduce
every private tracker's rules or minimum seeding requirements.

Snapshots batch ledger writes into one transaction and do not perform tracker
HTTP requests. The existing bounded metadata worker supplies evidence, so UI
polling does not add external API requests. The table is additive; older app
versions can run against the same database during rollback.
