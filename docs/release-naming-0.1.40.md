# Verified release names (0.1.40)

Own-release preparation now reads missing video scan metadata with ffprobe, uses standard raster quality tags for cropped progressive movies, and distinguishes observed x264/x265 encoders from AVC/HEVC codecs. Unverified scan types block naming instead of producing an unconventional dimensions tag. Encoder and DTS profile evidence survives preview and final-file validation.

The default audio stream supplies the release audio tag, distinguishing DTS core, DTS-HD HRA and DTS-HD Master Audio. Other streams remain in the media and MediaInfo. Converted in-place uploads cannot claim REMUX. Existing torrents and prepared payloads are not renamed or rewritten by this update.

Validation: 114 naming/upload tests passed, including missing MediaInfo scan metadata, cropped resolutions, encoder identity, primary DTS-HRA versus secondary DTS-MA, REMUX rejection, file preservation and failed-job rollback. ffprobe is already included in the application image's ffmpeg package on fresh installs and upgrades.
