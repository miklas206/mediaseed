# MediaSeed browserudvidelse 1.1.0

Send et magnetlink eller en `.torrent`-fil til din egen MediaSeed. Udvidelsen bruger dit normale login i en MediaSeed-fane og gemmer ingen adgangskoder, sessionscookies eller CSRF-nøgler. Ingen analytics eller eksterne tjenester. Den sender kun den fil/det link, du selv vælger, til din konfigurerede MediaSeed.

## Installation

Hent pakken fra **Browserudvidelse** øverst i MediaSeed. Siden foreslår en pakke ud fra browseren, men du kan ændre valget.

- **Chrome / Edge / Brave / Opera og andre Chromium-browsere:** Pak `mediaseed-chromium.zip` ud. Åbn browserens udvidelser (`chrome://extensions`, `edge://extensions`, `brave://extensions` eller `opera://extensions`), slå udviklertilstand til, vælg **Indlæs udpakket**, og vælg mappen med `manifest.json`.
- **Firefox desktop:** Pak `mediaseed-firefox.zip` ud. Åbn `about:debugging#/runtime/this-firefox`, vælg **Indlæs midlertidig tilføjelse**, og vælg `manifest.json`. Denne installation udløber ved genstart. Permanent installation i almindelig Firefox kræver Mozilla-signering; pakken er endnu ikke signeret eller publiceret i en butik.
- **Safari:** `mediaseed-safari-source.zip` er kilde til Apples WebExtension-konvertering, ikke en færdig Safari-app. Pak den ud på en Mac med Xcode, og kør `xcrun safari-web-extension-packager /sti/til/udpakket/mappe` (ældre Xcode: `xcrun safari-web-extension-converter /sti/til/udpakket/mappe`). Gennemgå værktøjets kompatibilitetsmeldinger, byg appen, og aktivér udvidelsen i Safari. Distribution kræver Apples signering. Safari og mobile browsere er ikke runtime-verificeret her. iOS Chrome/Edge har ikke samme manuelle udvidelsesinstallation som desktopversionerne.

## Brug

1. Åbn udvidelsen, indtast **MediaSeeds egen adresse** (domæne eller LAN-adresse med korrekt port), og vælg **Gem og forbind**. Eksempel: `https://seed.example.dk`. Brug samme adresse, som MediaSeed viser efter login/SSO. Dit certifikat skal være betroet af browseren; udvidelsen omgår ikke certifikatkontrol.
2. Log ind normalt i MediaSeed, hvis det bliver nødvendigt. Vend tilbage til udvidelsen og forbind igen. En eksisterende session genbruges.
3. Indsæt et magnetlink, vælg/træk en torrentfil, eller højreklik på et link → **Tilføj i MediaSeed…**. Vælg **Tilføj og start** eller **Tilføj på pause**. Start kræver verificeret VPN og en bruger med skriveadgang.
4. Vil du fange almindelige klik på en bestemt hjemmeside, åbnes udvidelsen fra den side, og **Aktivér på denne hjemmeside** vælges. Derefter åbner klik på magnetlinks og links med `.torrent` i stien tilføjelsesvinduet. Slå siden fra igen i listen. Andre downloadadresser håndteres via højreklik eller ved at vælge den hentede fil.

Hjemmesider med særlige downloadflows, omdirigeringer eller filadresser uden `.torrent` kan kræve, at du henter filen normalt først. Det gælder også private tracker-links, der ikke kan hentes i deres egen fanes login-session. Ingen automatisk gentagelse af POST ved timeout: kontrollér MediaSeed-listen før et nyt forsøg. Magnetmetadata kan være forsinket i torrentnetværket.

## Data og tilladelser

Ved tilføjelse kan **Automatisk oprydning** vælges: aldrig, seedingtid, uploadratio, begge eller ét af målene. Vælg separat, om kun torrenten eller også filerne skal fjernes. Standard er aldrig. Reglen gemmes i MediaSeed og kan slås fra under Detaljer; browseren behøver ikke forblive åben. MediaSeed skal køre for at udføre oprydningen. Eksisterende udvidelser skal genindlæses/opdateres til 1.1.0 for at vise felterne.

Kun MediaSeed-adressen og listen over aktiverede hjemmesider gemmes varigt lokalt. Browseren giver adgang til de enkelte hosts, når du beder om det; hosttilladelser gælder alle porte på samme host, mens udvidelsen selv kræver MediaSeeds præcise origin. Importer til gennemgang gemmes midlertidigt i browserens sessionlager (eller hukommelse, hvor sessionlager mangler), læses kun af udvidelsen og udløber efter ti minutter. Torrentlinket kan indeholde private tracker-parametre. Linkfangst sender ikke noget til MediaSeed, før du vælger en tilføjelsesknap. **Slå fra** stopper fangst; hostadgang kan også fjernes i browserens tilladelsesindstillinger.

Der er ingen webshop-/butikspublicering eller automatiske opdateringer. Hent en ny pakke fra din MediaSeed for at opdatere. Den fælles kode og særskilte manifester er testet med syntetiske browser- og API-transporter; faktisk installation i browserne er endnu ikke verificeret.

## Byg / test

Fra repositoriets rod: `python browser-extension/build.py`. Det bygger reproducerbare ZIP-filer i `mediaseed/static/extensions/`, der følger med appen. `python browser-extension/build.py --check` kontrollerer, at de indcheckede pakker svarer til kilden. JavaScript-tests: `node --test tests/extension.test.mjs tests/extension-download.test.mjs`.
