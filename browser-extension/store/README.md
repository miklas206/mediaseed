# Publishing checklist

The files here prepare a store submission. They are not an installed or approved store extension.

## Publisher steps required

1. Register a Chrome Web Store developer account, Microsoft Partner Center Edge Add-ons account and/or Mozilla Add-ons account. Registration, fees, publisher identity, contact details and terms must be completed by the account owner.
2. Review the text in `LISTING.md` and `PRIVACY.md`. Choose a public support contact and publish the privacy policy at a publicly reachable HTTPS URL. The user's LAN MediaSeed and private GitHub repository are not publicly reachable review/support endpoints.
3. Prepare a restricted demonstration/preview environment or reproducible reviewer instructions. Never share production/beta credentials or WireGuard keys. The UI screenshots here use synthetic settings only.
4. Upload the package, icon, screenshots and listing material. Complete current privacy/permission declarations accurately. Select the intended visibility (public or unlisted, where supported). The account owner must accept publisher agreements and supply required identity/payment details.
5. Submit for review. Do not label the extension available until the provider has signed/approved it and the published listing URL has been verified.
6. Record the approved store IDs/URLs and then change MediaSeed's browser-detected download button into the corresponding **Installér i browseren** link. Keep a manual development package as a separate fallback. Never invent or display an unapproved store URL.

## Package mapping

- Chrome Web Store and Edge Add-ons: `mediaseed/static/extensions/mediaseed-chromium.zip`.
- Mozilla Add-ons: `mediaseed/static/extensions/mediaseed-firefox.zip`. Mozilla signs the resulting XPI. Merely renaming ZIP to XPI does not make it installable in ordinary Firefox.
- Safari: the existing Safari-source ZIP must be converted and built on macOS/Xcode, then signed/distributed through the appropriate Apple workflow. It is not a submission-ready Safari application.

Run `python browser-extension/build.py --check`, `pytest tests/test_extension_packages.py`, and `node --test tests/extension.test.mjs tests/extension-download.test.mjs` before uploading. The store images must be reviewed after UI changes; regenerate them from the packaged popup in an isolated browser, never from the user's Chrome profile.

## Distribution constraints

A Windows helper may download/extract files but cannot silently install an ordinary Chrome extension outside the store. Chrome Windows/macOS self-hosting is restricted to enterprise policies. This project does not change browser policies or disable signing checks. Firefox self-distribution is supported after Mozilla signing. Safari needs an Apple-signed app, not a renamed archive.

## Official references (checked 12 October 2026)

- [Chrome developer registration](https://developer.chrome.com/docs/webstore/register/)
- [Chrome distribution](https://developer.chrome.com/docs/extensions/how-to/distribute)
- [Chrome store images](https://developer.chrome.com/docs/webstore/images)
- [Microsoft Edge publishing](https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/publish-extension)
- [Mozilla signing and distribution](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/)
- [Mozilla self-distribution](https://extensionworkshop.com/documentation/publish/self-distribution/)
