"""Explicit same-origin assets for the built-in WebUI typography choices."""

WEB_TYPOGRAPHY_FONTS = frozenset({
    "share-tech-mono.ttf", "share-tech-mono-OFL.txt",
    "ibm-plex-mono-regular.ttf", "ibm-plex-mono-medium.ttf", "ibm-plex-mono-OFL.txt",
    "barlow-condensed-regular.ttf", "barlow-condensed-medium.ttf", "barlow-condensed-OFL.txt",
    "orbitron-variable.ttf", "orbitron-OFL.txt", "audiowide.ttf", "audiowide-OFL.txt",
    "atkinson-next-variable.ttf", "atkinson-next-OFL.txt",
    "atkinson-mono-variable.ttf", "atkinson-mono-OFL.txt",
    "jetbrains-mono-variable.ttf", "jetbrains-mono-OFL.txt",
    "source-code-pro-variable.ttf", "source-code-pro-OFL.txt",
})
WEB_TYPOGRAPHY_READ_PATHS = frozenset({
    "/assets/theme-typography.css", "/assets/theme-typography.js",
    *(f"/assets/fonts/{name}" for name in WEB_TYPOGRAPHY_FONTS),
})
