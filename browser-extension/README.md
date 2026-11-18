# MediaSeed browserudvidelse 1.6.1

Én højreklikshandling: **Send til MediaSeed**. RSS-links genkendes på feedruten eller RSS/XML/Atom-endelsen; øvrige HTTP-downloadlinks hentes som torrentfiler, og magnetlinks valideres. RSS-links hentes ikke af udvidelsen. De åbnes i MediaSeeds RSS-formular. Torrentfiler og magneter åbnes i MediaSeeds tilføjelsesformular. Start/pause, mappe, seedtid, ratio og oprydning vælges og bekræftes kun i MediaSeed.

Udvidelsen finder og fokuserer en eksisterende MediaSeed-fane eller åbner den gemte adresse. Ved login venter linket i browsersessionen og leveres efter login. Ved en åben dialog bevares linket; luk dialogen og tryk Fortsæt i udvidelsen. Ét link kan vente ad gangen, i højst 15 minutter. Ryd sletter kun det ventende link; ingen download er startet.

Opsætningen har et bredere adressefelt og knappen Gem og åbn MediaSeed. Ingen downloadindstillinger eller separate RSS-knapper findes i udvidelsen. Adressen gemmes lokalt. Private links og torrentbytes gemmes kun i sessionshukommelsen, aldrig i en destinations-URL, synkroniseret lager eller permanent fejlhistorik. Ved manglende sessionslager bruges flygtig hukommelse. Normalt MediaSeed-login og serverens VPN-/lagerkontroller bevares.

## Installation og opdatering

Hent pakken i MediaSeed → Browserudvidelse, pak den ud og indlæs den udpakket under chrome://extensions (eller tilsvarende Chromium-browser). Ved opdatering erstattes filerne i den eksisterende udvidelsesmappe, og Genindlæs vælges. Den gemte MediaSeed-adresse bevares. Ældre start-/oprydningsvalg i browserlager ignoreres; ingen serverindstillinger migreres eller overskrives. Genindlæs gamle trackerfaner for at fjerne tidligere klikopsamling.

Firefox-pakken er til midlertidig indlæsning under about:debugging og kræver Firefox 140+. Safari-pakken er kildekode til Apple-pakning, ikke en signeret Safari-app. Der er ingen automatisk butiksopdatering. Byg pakker med `python browser-extension/build.py` og kontrollér dem med `--check`.

## Flere torrents direkte i MediaSeed

Højreklik på et torrent-downloadlink og vælg **Send til MediaSeed**. Gentag for de næste links; filerne samles i MediaSeeds åbne tilføjelsesvindue. Mappe og indstillinger bevares. Hurtige højrekliksoverførsler behandles i rækkefølge. Ingen separat udvidelsesfane eller batchvælger åbnes. Torrentdownloads starter først, når brugeren bekræfter i MediaSeed.

Ved en ny udvidelsesinstallation skal adressen og browseradgangen gemmes én gang via værktøjslinjens MediaSeed-ikon. Opsætning åbner kun ikonets popup; hvis browseren blokerer automatisk åbning, viser ikonet en besked, og det aktuelle link venter i sessionshukommelsen. En entydig genkendelig MediaSeed-fane kan genbruges automatisk, hvis dens vært allerede har fået browseradgang. Udvidelsen anmoder ikke om bred læseadgang til faner/historik.


## Safari på iPhone og iPad (1.6.1)

Safari-pakken indeholder nu trykbetjening: Find links på siden, indsæt torrent-/magnet-/RSS-link, eller vælg en .torrent-fil fra Filer. Højreklik er ikke nødvendigt. iOS-pakken bruger en ikke-vedvarende baggrund og anmoder ikke om contextMenus, som iOS ikke understøtter. Downloadvalg bekræftes fortsat i MediaSeed.

Dette er kildekode, ikke en installerbar iPhone-app. Den er kontrolleret med automatiserede tests og mobile skærmstørrelser; fysisk Safari/iPhone-test og Apple-signering mangler. Chrome på iPhone kan ikke installere denne udvidelse.

Med Apple Developer-medlemskab kan Safari-ZIP-pakken uploades til Safari Web Extension Packager i App Store Connect: Opret appen til iOS, vælg Users and Access → Xcode Cloud → Safari Web Extension Packager, upload hele pakken, gennemgå kompatibilitetsrapporten og vælg buildet i TestFlight. En Mac er ikke nødvendig for denne distributionsvej. Alternativt kan pakken konverteres og bygges lokalt med Xcode på Mac.

Efter installation: Aktivér MediaSeed i Indstillinger → Apps → Safari → Udvidelser. Åbn trackeren i Safari, vælg MediaSeed fra sidemenuen, gem MediaSeed-adressen og giv adgang til de ønskede sider. Vælg Find links på siden, tryk på downloadlinket, og bekræft indstillingerne i MediaSeed. Apple-installationen kan ikke erstattes af at hente ZIP-filen i Safari.

Officielle vejledninger:
- https://developer.apple.com/documentation/safariservices/packaging-and-distributing-safari-web-extensions-with-app-store-connect
- https://developer.apple.com/documentation/safariservices/assessing-your-safari-web-extension-s-browser-compatibility
- https://support.google.com/chrome_webstore/answer/1698338
