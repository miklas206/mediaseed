# MediaSeed browserudvidelse 1.3.0

Send magnetlinks og torrentfiler direkte til din egen MediaSeed med gemte standardvalg. Logoet vises i værktøjslinjen og udvidelseslisten. Udvidelsen genbruger dit normale login i en MediaSeed-fane og gemmer ingen adgangskoder, cookies eller CSRF-nøgler. Ingen analytics eller eksterne tjenester.

Version 1.3.0 fanger også NordicBytes' `/torrents/download/...`-links uden `.torrent` i adressen, når hjemmesiden er aktiveret. Filen hentes med kildesidens normale login og sendes med gemte start-/seedingvalg; en popup på kildesiden bekræfter et accepteret svar fra MediaSeed. Andre NordicBytes-sider og almindelige downloads overtages ikke. Efter manuel opdatering skal NordicBytes-fanen genindlæses, så den nye klikfangst indlæses.

## Installation og opdatering

Hent pakken fra **Browserudvidelse** øverst i MediaSeed. Downloadsiden vælger pakke efter browser; valget kan ændres.

- **Chrome / Edge / Brave / Opera:** Udpak `mediaseed-chromium.zip`. Åbn browserens udvidelser, slå udviklertilstand til, vælg **Indlæs udpakket**, og vælg mappen med `manifest.json`. Ved opdatering erstattes filerne i den samme mappe, og udvidelsen genindlæses. Adressen og gemte valg bevares under samme udvidelses-ID.
- **Firefox desktop:** Udpak `mediaseed-firefox.zip`. Åbn `about:debugging#/runtime/this-firefox`, vælg **Indlæs midlertidig tilføjelse**, og vælg `manifest.json`. Kræver Firefox 140+. Midlertidig installation udløber ved genstart. Permanent almindelig Firefox-installation kræver Mozilla-signering; pakken er ikke signeret eller publiceret.
- **Safari:** `mediaseed-safari-source.zip` er kilde til Apples WebExtension-konvertering, ikke en færdig Safari-app. Udpak på en Mac med Xcode og brug `xcrun safari-web-extension-packager /sti/til/mappe` (ældre Xcode: `safari-web-extension-converter`). Gennemgå kompatibilitetsmeldinger og byg appen. Distribution kræver Apples signering. Safari/mobile browsere er ikke runtime-verificeret. iOS Chrome/Edge understøtter ikke desktop-installationsmetoden.

## Forbind én gang

1. Åbn udvidelsen, mens du står på MediaSeed eller dens downloadside. Adressen udfyldes automatisk. Fra andre sider kan adressen indtastes under **Forbindelse**. Brug MediaSeeds egen origin, inklusive port; både domæne og LAN-adresse understøttes uden hardkodet server.
2. Tryk **Gem og forbind**, og giv browseradgang. Certifikatet skal være betroet; certifikatkontrol omgås ikke.
3. Log ind normalt, hvis din MediaSeed-session er udløbet. Allerede aktive sessioner genbruges; ingen separat udvidelsesadgangskode kræves.

## Gem dine standardvalg

Åbn **Indstillinger**, vælg start med det samme eller pause, ratio, automatisk oprydning, seedtimer og om filer skal bevares, og tryk **Gem standardindstillinger**. Oversigten viser de gemte valg. Ikke-gemte ændringer markeres tydeligt og bruges først efter gem. Indstillingerne bevares, når popupen lukkes, browseren genstartes, eller en torrent tilføjes.

Oprydning kan være aldrig, tid, ratio, BÅDE eller ENTEN. **Aldrig** og filbevaring er standard. Automatisk filsletning kræver eksplicit valg og gem; det fremgår af oversigten. Regler gemmes i MediaSeed pr. ny import og kan slås fra under Detaljer. Browseren behøver ikke være åben; MediaSeed skal køre. Uden oprydning bruges de eksisterende stopmål ratio / 49 timer.

## Direkte tilføjelse

- **Højreklik på magnet- eller torrentlink → Tilføj i MediaSeed.** Linket sendes med de gemte valg uden ekstra formular. Torrentfilen hentes i hjemmesidens egen fanes login-session, når muligt.
- **Almindelige klik:** Aktivér linkfangst for hjemmesiden i udvidelsen én gang. Klik på magnetlinks eller links med `.torrent` i stien sendes derefter direkte. Fjern hjemmesiden fra listen for at slå fangst fra.
- **Indsæt eller træk ind:** Indsæt et magnetlink eller vælg/træk en torrentfil ind i popupen. Den primære knap bruger dit gemte startvalg; den anden knap kan ændre start/pause for denne import. De gemte oprydningsvalg bruges stadig.

En kort kvittering på hjemmesiden og `OK` på ikonet viser accept. `!` og en gemt fejlstatus viser problemer. Hvis opsætning, adgang eller login mangler, åbnes en klargjort formular med det oprindelige link. Ingen automatisk gentagelse af POST ved fejl: kontrollér listen i MediaSeed før et nyt forsøg. En fejl i kvitteringen efter en accepteret import udløser aldrig gentagelse.

Start kræver stadig verificeret VPN og skriveadgang. Særlige downloadflows, redirects, links på en anden host eller private trackere kan kræve engangsadgang til kildens host eller normal download efterfulgt af filvalg. Magnetmetadata kan være forsinket i torrentnetværket.

## Data, tilladelser og verifikation

Kun MediaSeed-origin, aktiverede hjemmesider, standardindstillinger og kort seneste status med tidspunkt gemmes varigt lokalt. Torrentlinks/filer til fejlretning gemmes i sessionlager eller hukommelse, udløber efter ti minutter og gemmes aldrig varigt. En linkadresse kan indeholde private tracker-parametre og vises ikke i status.

Browseradgang gives pr. host. Browserens hosttilladelser omfatter alle porte på hosten; udvidelsen håndhæver MediaSeeds præcise origin. Hjemmesider kan ikke ændre standardvalg eller sende vilkårlige API-kald. Klikfangst begrænses til aktiverede hjemmesider, øverste frame og reelle brugerklik. Ingen nye obligatoriske hosttilladelser er tilføjet.

Der er ingen butikspublicering eller automatisk opdatering. Hent den nye pakke og genindlæs udvidelsen. Tests bruger syntetiske browser-/API-transporter for direkte afsendelse, vedvarende valg, kildefil-login, afviste afsendere og fejl uden gentagelse. PNG-ikoner og pakkernes indhold kontrolleres. Faktisk installation i browserne er ikke verificeret her.

## Byg

`python browser-extension/build.py` bygger reproducerbare ZIP-filer i `mediaseed/static/extensions/`. `--check` kontrollerer indcheckede pakker. PNG-ikoner er eksporteret fra MediaSeeds eksisterende vektorgeometri; på Windows kan `build-icons.ps1` genopbygge dem med System.Drawing. Python-pakkebyg kræver ingen grafiske afhængigheder.

Tests: `node --test tests/extension.test.mjs tests/extension-download.test.mjs` og `pytest tests/test_extension_packages.py`.
