# MediaSeed browserudvidelse 1.5.0

Én højreklikshandling: **Send til MediaSeed**. RSS-links genkendes på feedruten eller RSS/XML/Atom-endelsen; øvrige HTTP-downloadlinks hentes som torrentfiler, og magnetlinks valideres. RSS-links hentes ikke af udvidelsen. De åbnes i MediaSeeds RSS-formular. Torrentfiler og magneter åbnes i MediaSeeds tilføjelsesformular. Start/pause, mappe, seedtid, ratio og oprydning vælges og bekræftes kun i MediaSeed.

Udvidelsen finder og fokuserer en eksisterende MediaSeed-fane eller åbner den gemte adresse. Ved login venter linket i browsersessionen og leveres efter login. Ved en åben dialog bevares linket; luk dialogen og tryk Fortsæt i udvidelsen. Ét link kan vente ad gangen, i højst 15 minutter. Ryd sletter kun det ventende link; ingen download er startet.

Opsætningen har et bredere adressefelt og knappen Gem og åbn MediaSeed. Ingen downloadindstillinger eller separate RSS-knapper findes i udvidelsen. Adressen gemmes lokalt. Private links og torrentbytes gemmes kun i sessionshukommelsen, aldrig i en destinations-URL, synkroniseret lager eller permanent fejlhistorik. Ved manglende sessionslager bruges flygtig hukommelse. Normalt MediaSeed-login og serverens VPN-/lagerkontroller bevares.

## Installation og opdatering

Hent pakken i MediaSeed → Browserudvidelse, pak den ud og indlæs den udpakket under chrome://extensions (eller tilsvarende Chromium-browser). Ved opdatering erstattes filerne i den eksisterende udvidelsesmappe, og Genindlæs vælges. Den gemte MediaSeed-adresse bevares. Ældre start-/oprydningsvalg i browserlager ignoreres; ingen serverindstillinger migreres eller overskrives. Genindlæs gamle trackerfaner for at fjerne tidligere klikopsamling.

Firefox-pakken er til midlertidig indlæsning under about:debugging og kræver Firefox 140+. Safari-pakken er kildekode til Xcode, ikke en testet eller signeret Safari-app. Der er ingen automatisk butiksopdatering. Byg pakker med `python browser-extension/build.py` og kontrollér dem med `--check`.

## Flere torrents samlet

Åbn udvidelsen og vælg **Send flere torrents…**. En almindelig udvidelsesfane åbnes, så valgte filer bevares, når MediaSeed får fokus. Vælg flere .torrent-filer og/eller indsæt torrent-downloadlinks, ét pr. linje, og tryk **Send valgte til MediaSeed**. Op til 100 poster og 64 MiB lokale filer, 4 MiB pr. fil. RSS/magnetlinks bruger fortsat højreklik.

Torrents overføres én ad gangen og samles i MediaSeeds åbne tilføjelsesvindue. Mappe, start/pause og oprydning vælges samlet dér. Ingen downloads startes af udvidelsen. En anden åben dialog, login eller fejl stopper overførslen. Hold udvidelsesfanen åben, ret problemet og tryk Send valgte igen; bekræftede poster springes over. Valgte filer og links ligger kun i fanens hukommelse; kun den aktuelle afventende overførsel gemmes i eksisterende sessionslager i højst 15 minutter. Lukning/genindlæsning af fanen kræver nyt valg af de resterende filer.
