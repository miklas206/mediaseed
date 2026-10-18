# Automatisk oprydning

Ved en ny manuel import (magnetlink eller torrentfil), også fra browserudvidelse 1.1.0, kan brugeren vælge:

- `never`: ingen automatisk oprydning; torrent og filer bevares. Ingen automatisk tids- eller ratiogrænse; seeding fortsætter, indtil brugeren stopper eller fjerner torrenten.
- `time`: efter den valgte aktive seedingtid.
- `ratio`: efter den valgte qBittorrent-uploadratio.
- `both`: først når både tid og ratio er nået.
- `either`: når mindst ét af målene er nået.

Brugeren vælger separat **kun torrenten** (filer bevares) eller **torrenten og filerne** (irreversibel sletning). Standard er `never`, og standardhandlingen ved aktivering bevarer filerne. Reglen kan ses og slås fra under Detaljer. Gamle torrents og RSS-regler får ingen oprydningsregel automatisk.

Tid er qBittorrents seedingtæller, ikke tid siden tilføjelse. Der slettes kun færdige torrents med gyldige målinger, kendt peerstatus, nul tilsluttede downloadere og nul uploadhastighed. Kontrol foretages ca. hvert 15. sekund, mens MediaSeed kører og klienten er tilgængelig. Når trackerkreditering er konfigureret, bruger oprydning den krediterede ratio.

## Håndhævelse

En additiv SQLite-tabel `torrent_cleanup` gemmer aktive regler. Et tilfældigt importmærke i qBittorrents tags og et præcist info-hash identificerer den ene tilladte torrent. Hash beregnes af torrentfilens oprindelige info-dictionary eller magnetlinkets BTIH/BTMH. Rene v2-torrents håndteres med fuldt og trunkeret SHA256-hash. Dubletter afvises, hvis oprydning vælges; eksisterende torrents ændres ikke af den import. Manglende/forkert mærke blokerer oprydningen. Der bruges aldrig titelmatch, wildcard-hashes eller direkte filsystem-sletning.

Importen sker på pause. Supervisoren slår native ratio-/tidsstop fra på netop den mærkede torrent, før eventuel start gennem den friske VPN-gate. Det forhindrer, at native ELLER-stop hindrer BÅDE-målet. Forsinket metadata forbliver gemt som afventende; ingen automatisk start før klargøring. Klient- eller statusfejl giver ingen sletning. Bekræftet delete-accept fjerner reglen, så et senere genimporteret hash ikke arver den. Manuelt Fjern glemmer også reglen under samme lokale lås.

Ved annullering fjernes reglen varigt først. Native stopmål slås fra, hvis den samme mærkede torrent kan bekræftes. Ved utilgængelig klient er oprydningen stadig slået fra; startadapteren og baggrundsarbejderen fjerner gamle stopmål, når klienten igen er klar. Maks. 50 samtidige oprydningsregler. Midlertidig klargøring af importer uden oprydning tæller ikke med og fjernes efter klargøring. Kør én MediaSeed-worker; låsen er lokal til processen.

## API og verifikation

Magnet-POST accepterer `cleanup: {mode, hours, ratio, delete_files}`. Filupload accepterer tilsvarende query-parametre `cleanup_mode`, `cleanup_hours`, `cleanup_ratio`, `cleanup_delete_files`. Udeladelse vælger `never` med ubegrænset seeding. Timer skal være mellem ét minut og 87600 timer; ratio mellem 1 og 1000; ukendte modes afvises. Detaljer inkluderer filtreret `cleanup`; `POST /api/torrents/{hash}/cleanup/cancel` slår reglen fra med normal session, rolle- og CSRF-kontrol.

MediaVPN tillader kun det afgrænsede cleanup-tag ved import og præcis `-1/-1/-1` for `setShareLimits`. Start og alle mutationer beholder kontrol af frisk VPN-readiness under MediaVPN-låsen. Ingen tunneler, profiler eller globale qBittorrent-indstillinger ændres.

Tests bruger syntetiske transporter og metadata for tid/ratio/BÅDE/ENTEN, genstart, forsinket metadata, filbevaring/sletningsflag, afviste dubletter, identitetsbrud, peeraktivitet, ugyldige målinger, API-validering og klientfejl.

`tests/verify_cleanup_client.py` er desuden kørt mod den installerede qBittorrent 5.2.4-image i en separat engangscontainer med `--network none`, uden live datamounts. En genereret én-byte-torrent verificerer info-hash, tags, ubegrænsede stopmål og begge slettehandlinger. qBittorrent 5.2 kræver `shareLimitAction=Stop` ved `setShareLimits`; dette felt sættes eksplicit, så klientens native handling ikke sletter filer.

Ved tilbagerulning til en version uden supervisoren skal aktive oprydningsregler først annulleres under Detaljer. En ældre version læser ikke tabellen, men torrents kan stadig have ubegrænsede native stopmål. Databasen og de oprindelige app-images bevares som rollback; ingen downloads flyttes eller slettes af opgraderingen.

Adfærdsreference: [qlerup/fjordseed](https://github.com/qlerup/fjordseed/tree/c32c141069bc4f6133533b0026a881400a56f8cf), gennemgået 2026-10-12 (`templates/_seeding_fields.html`, `qbit_rpc.py`). Ingen implementation kopieret. De fem kombinationsvalg følger brugerens skærmbillede.

Fra 0.1.28 fjerner en idempotent baggrundsarbejder også gamle native stopgrænser på eksisterende torrents uden oprydning, også uden en tracker-konto. Den gennemløber sider på 500 torrents og ændrer højst otte pr. cyklus. Den starter aldrig en brugerpauset torrent som led i opgraderingen. Ingen profiler, credentials eller mediefiler ændres.

Aktuel reference: qlerup/fjordseed main, `bcb82ac40efc6a4b9a5d06d2abf45168c2e98489`, verificeret 2026-10-18. `qbit_rpc.apply_limits` slår native stop fra, og `seeding_completion` lader beholdte filer seede ubegrænset. MediaSeed bruger sin eksisterende sikre pause/klargør/start-vej for at være kompatibel med MediaVPNs afgrænsede RPC.
