# MediaSeed browser extension privacy policy

Effective date: 12 October 2026. Applies to MediaSeed browser extension 1.2.0.

## Purpose

The extension sends magnet links and torrent files selected by you to a MediaSeed server chosen and controlled by you. It does not provide torrent content, a public download service or a VPN subscription.

## Information used

- The MediaSeed server origin, enabled website origins, start/seeding/cleanup preferences and a short recent operation status are stored in the browser's local extension storage. These settings are not synchronised through this extension.
- A selected magnet link or torrent file is processed to perform your requested import. Links can contain private tracker parameters. Pending imports are held in session storage, or volatile memory if session storage is unavailable, with a ten-minute expiry. They are not saved in persistent extension storage. Closing the browser clears session-only pending imports.
- With your permission, the extension reads the URL of a clicked magnet or torrent link on a website you enabled. Other browsing activity is not recorded. A context-menu action can also select a link without enabling automatic capture.
- A torrent file may be downloaded through its source tab to use that website's existing login. File content is limited to 4 MiB. The extension does not read account passwords.
- The extension runs a limited same-origin bridge in your MediaSeed tab. It checks your session and obtains a fresh CSRF token to submit the import. Passwords, cookies and CSRF tokens are not returned to the extension background script or persisted by the extension.

## Where information goes

Selected torrent information is sent to your configured MediaSeed server. A source website receives the file download request you selected. The extension developer receives no analytics, telemetry, browsing history or copies of your imports through the extension. There are no advertising, tracking or data-sale integrations. Browser-store installation/update services remain subject to the browser provider's own policies.

Your MediaSeed server and source websites have their own retention and access rules. The extension cannot delete data already accepted by a server merely by clearing browser storage.

## Controls and deletion

You choose the destination, grant host access and can disable capture for each website. Host permissions can be revoked in the browser. Uninstalling the extension removes its local settings according to browser behaviour. Pending imports expire after ten minutes; expired records are purged on subsequent pending-import activity or when browser session storage is cleared.

Automatic removal of downloaded content is disabled by default. If you explicitly save a rule that deletes files, it applies to future imports and is executed by MediaSeed on your server. You can inspect or cancel the rule in MediaSeed. Closing or uninstalling the extension does not cancel a server-side cleanup rule.

## Contact

Use the support contact shown on the official browser-store listing for privacy questions. The publisher must provide that contact before publication. An unpublished development copy has no store listing yet.
