# Store listing material — MediaSeed 1.2.0

Status: prepared for publisher review; not submitted, signed or published. No store IDs or live installation links exist yet.

## Name

MediaSeed

## Short description (Danish)

Send magnetlinks og torrentfiler direkte til din egen MediaSeed med gemte valg for start, seedtid, ratio og oprydning.

## Full description (Danish)

Tilføj torrents til din egen MediaSeed direkte fra browseren.

Højreklik på et magnet- eller torrentlink og vælg Tilføj i MediaSeed. Udvidelsen bruger dine gemte standardvalg, så du ikke skal udfylde en formular ved hver tilføjelse. Du kan også indsætte magnetlinks, vælge eller trække en torrentfil ind i udvidelsen og aktivere almindelige klik på bestemte hjemmesider.

Vælg selv:
- Start med det samme eller tilføj på pause.
- Aktiv seedingtid og uploadratio.
- Ingen automatisk oprydning, tid, ratio, begge mål eller ét af målene.
- Behold downloadede filer eller fjern dem sammen med torrenten.

Oprydning er slået fra som standard. Når den vælges, udføres reglen på din MediaSeed-server og kan ses eller slås fra under Detaljer. Start kræver en verificeret VPN-forbindelse i MediaSeed.

Du skal have din egen MediaSeed-server. Forbind udvidelsen én gang; dit normale MediaSeed-login genbruges. Udvidelsen gemmer ingen adgangskoder og sender ingen analytics til udvikleren. Browseradgang gives til din MediaSeed og de hjemmesider, du vælger. Domæne og LAN-adresse understøttes; HTTPS-certifikatet skal accepteres/betroes af browseren.

Udvidelsen indeholder ikke torrentindhold, en VPN-tjeneste eller en indbygget torrentklient. Særlige downloadflows og redirects på nogle hjemmesider kan kræve, at filen hentes normalt først.

## Single purpose / permission justifications (English)

Single purpose: Send user-selected magnet links or torrent files to the user's own MediaSeed server, using the user's saved import preferences.

- `storage`: Persist the configured server origin, explicitly enabled website origins, import preferences and recent non-sensitive operation status. Pending imports use session storage only.
- `contextMenus`: Provide the user-initiated Add to MediaSeed action on links.
- `activeTab`: Access the current tab following an explicit user action, to discover the user's MediaSeed origin, read a selected torrent file in its website session and show operation feedback.
- `scripting`: Inject packaged, self-contained scripts in the user's MediaSeed tab for authenticated same-origin submission, in a selected source tab for file fetching, and in explicitly enabled sites for link capture. No remotely hosted executable code is used.
- Optional HTTP/HTTPS host access: Users may self-host MediaSeed on any domain, LAN address or port. Access is requested for the particular server or website, not granted across all sites on installation. The bridge enforces the exact configured MediaSeed origin even though browser host patterns cover all ports.

## Privacy declarations to review before submission

Do not claim that the extension never handles user data: it processes user-selected links/files, enabled-site origins and the user's server session for its single purpose. It does not transmit them to the developer. Answer the store's current questionnaire consistently with `PRIVACY.md`, including website content/user-selected links where applicable. Never claim an independent security certification or completed store approval.

The publisher must supply an accessible public privacy-policy URL and support contact. The current GitHub repository returns 404 to anonymous requests and cannot serve as the public policy URL without a separate publishing decision. Do not change repository visibility automatically.

## Images

- Extension icon: `../icons/128.png`.
- Small Chrome promotional tile: `images/small-promo-440x280.png`.
- Screenshot 1: `images/01-direct-add.png` (1280×800).
- Screenshot 2: `images/02-seeding-settings.png` (1280×800).

Screenshots render the actual packaged popup in an isolated Firefox instance with synthetic settings and an example server origin. They demonstrate the UI; they do not demonstrate installation from a store or a live torrent transfer. No real user data appears in the images.

## Reviewer notes

The extension requires a self-hosted MediaSeed server; a public store listing does not include a hosted account. Reviewers need a reproducible preview setup or a separate restricted test instance supplied by the publisher. Do not provide the user's beta session, VPN credentials, private tracker URLs or real data to reviewers. Source is plain JavaScript; `build.py` copies files and generates browser-specific manifests without minification or remote executable dependencies. Provide the corresponding source package if the store requests it.
