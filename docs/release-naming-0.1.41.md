# MediaSeed 0.1.41 – evidenced release names

New drafts expose naming checks in the preview and include them in the final review gate. Own releases use TMDB title before cached/manual titles; final readiness requires the selected catalogue identity and movie year. Numeric catalogue titles are retained. Edition claims require explicit source confirmation and must agree with edition markers present in selected filenames. Source conflicts require correction; source provenance is never inferred from the encoded video codec.

Main audio excludes commentary/audio description and requires an unambiguous default when multiple main tracks exist. Preview and final MediaInfo projections retain the necessary role flags. Forced/signs/commentary subtitles do not establish Nordic language coverage; full SDH subtitles remain eligible. Only embedded or explicitly selected-to-be-embedded subtitles contribute, with final stream verification after muxing.

Double episodes cannot be disguised through a single-episode override. Partial season requests must pass automatic catalogue planning; specials remain S00 episodes and cannot disappear inside a regular season range. Other-group ownership rules remain unchanged. Existing generated-name comparison, exact payload verification and NFO/torrent construction use the final artifacts; integration tests verify identical attachment/payload NFO and matching torrent paths.

No database/configuration migration, new package, container mapping or media rewrite. Old jobs and seeding torrents are not renamed or rebuilt. Existing final metadata may be incomplete; explicit correction is required for new drafts rather than guessing. Double-episode files currently require separate episode files; combined numbering is not implemented.

Validation: 136 focused synthetic pytest cases passed, covering naming, subtitle/ownership handling, NFO, torrent seeding, in-place rollback, conversion installs, storage installs and legacy cleanup upgrade. JavaScript syntax check passed. This is not a claim that unrelated legacy test failures are fixed.
