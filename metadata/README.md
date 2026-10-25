`enrich.py` downloads AppStream catalogs from Flathub, Debian, ALT Sisyphus and
Fedora Rawhide, then checks upstream project pages for missing descriptions and
screenshots. It preserves native package names, desktop launchers, icons, licenses
and release histories. Generated snapshots, source attribution and audit reports
are stored in `work/enrichment/` and uploaded as CI artifacts; they are not
maintained in this directory.

`matches.json` records explicit native-package-to-Flathub IDs where names or
desktop IDs differ. These small mappings replace copied Flathub metainfo files
for Spotify, AnyDesk, AppFlowy, Zotero, ONLYOFFICE, Minecraft Launcher and Snoop.

The remaining per-package files are fallback metainfo or icon resources absent
from the automatically matched catalogs. This includes the Craft applications,
spotify-qt, Discord Chat Exporter, Terasology and the Minecraft launcher icon.
`sources.json` records their provenance and the licenses declared for the
supplemental metadata. Referenced images retain their upstream licenses.

`screenshot_sources.json` contains reviewed upstream source references for apps
whose AppStream catalogs have no screenshots or where a reviewed source is more
accurate than the project gallery. Catalog screenshots take precedence; reviewed
images then take precedence over generic page discovery. These entries add only
images and captions, preserving native descriptions, licenses and launchers. A
reviewed Steam app URL identifies the exact app while screenshots are fetched and
validated from Steam automatically, including for non-game software. Mirrored
Flathub screenshots can also replace moving GitHub-branch URLs, avoiding links
broken by branch renames. For itch.io galleries, `exclude_images` can omit a cover
image or logo while leaving the remaining screenshots to automatic discovery.

ALT metadata is read from the `appstream-data-desktop` RPM, and Fedora metadata
from Fedora's `appstream-data` RPM; neither package is installed. Their current
RPMs are resolved from the official package indexes and the extracted catalogs
are recorded in source reports.

To package existing generator exports using local upstream snapshots:

```sh
python generate.py package --enrichment-catalog work/enrichment/sources/flathub.xml.gz \
  --enrichment-extra-catalog work/enrichment/sources/debian-main.xml.gz \
  --enrichment-extra-catalog work/enrichment/sources/debian-contrib.xml.gz \
  --enrichment-extra-catalog work/enrichment/sources/debian-non-free.xml.gz \
  --enrichment-extra-catalog work/enrichment/sources/altlinux-sisyphus.xml.gz \
  --enrichment-extra-catalog work/enrichment/sources/fedora-rawhide.xml.gz
```

Ambiguous identities and sources with no additional data are recorded in
`audit.json`. Source catalog status is in
`work/enrichment/sources/status.json`. A missing match is not permission to copy
another application's screenshots. Reference packages from Arch are added to the LunaOS catalog only
while present in the current reference databases; Arch-owned catalog files are
not replaced. Pamac uses the repository order from pacman.conf, so LunaOS should
precede Arch repositories, as in the LunaOS default configuration.
