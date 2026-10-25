`enrich.py` downloads upstream AppStream data during catalog packaging. It fills
missing descriptions and screenshots while preserving native package names,
desktop launchers, icons, licenses and release histories. Generated snapshots,
source attribution and audit reports are stored in `work/enrichment/` and uploaded
as CI artifacts; they are not maintained in this directory.

`matches.json` records explicit native-package-to-Flathub IDs where names or
desktop IDs differ. These small mappings replace copied Flathub metainfo files
for Spotify, AnyDesk, AppFlowy, Zotero, ONLYOFFICE, Minecraft Launcher and Snoop.

The remaining per-package files are fallback metainfo or icon resources absent
from the automatically matched catalogs. This includes the Craft applications,
spotify-qt, Discord Chat Exporter, Terasology and the Minecraft launcher icon.
`sources.json` records their provenance and the licenses declared for the
supplemental metadata. Referenced images retain their upstream licenses.

`screenshot_sources.json` contains reviewed upstream image references for apps
whose AppStream data has no screenshots. These entries add only images and
captions, preserving the native descriptions, licenses and launchers. Mirrored
Flathub screenshots can also replace moving GitHub-branch URLs, avoiding links
broken by branch renames.

To inspect all installed catalogs without downloading the source again:

```sh
python enrich.py --flathub-catalog work/enrichment/flathub.xml.gz
```

To package existing generator exports using a local upstream snapshot:

```sh
python generate.py package --enrichment-catalog work/enrichment/flathub.xml.gz
```

Ambiguous identities and sources with no additional data are recorded in
`audit.json`. A missing match is not permission to copy another application's
screenshots. Reference packages from Arch are added to the LunaOS catalog only
while present in the current reference databases; Arch-owned catalog files are
not replaced. Pamac uses the repository order from pacman.conf, so LunaOS should
precede Arch repositories, as in the LunaOS default configuration.
