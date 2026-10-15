# Tracker summary and rank requirements — 0.1.20

Downloads shows the cached tracker-wide upload and download counters alongside ratio, buffer, account name, warnings and the automatic rank. These are account totals, not the current MediaSeed session. Clicking the card opens Trackers.

The same existing /api/user response supplies NordicBytes autogroup.title, uploadgroup.title and internalgroup.title. Only bounded plain-text titles are exposed; icon/color/extra group fields are discarded. A plain UNIT3D group string is supported as a fallback. Dashboard reads reuse the existing five-minute account cache and never trigger extra tracker requests. Errors clear rank data with the other account values.

The rank requirement table was transcribed from the user's NordicBytes screenshot supplied 2026-10-15. Explorer, Contributor, Pirate, Veteran, Guru and Legend are the listed upload path. Archivist is a separate seeding path. A successor is shown only for a recognized listed rank; unlisted ranks show the table without guessing their successor. No privileges or promotion are inferred from a target being met.

True upload must not be equated with credited account upload. The observed API has no true-upload, membership-age, comments or post counters. These remain unknown, not zero, and no remaining amount is fabricated. The documented seeding_size counter is used for the Archivist size requirement; avg_seedtime is not used without a confirmed unit/meaning. Requirements and measured gaps appear under Trackers, with a link to the official table so users can verify changes. The rank table is source-versioned, not fetched on a timer.

No schema or credential migration is needed. Fresh installs and existing accounts use the same additive response fields. Older clients ignore them. Tests use synthetic account responses and assert cache reuse, privacy, unknown-counter handling and the supplied thresholds. Real production installation is separate from local checks.
