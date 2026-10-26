#!/usr/bin/env python3
"""Automatically audit and enrich native catalogs using upstream AppStream data.

Native package IDs, launchers, icons, licenses and release histories are retained.
Generated catalogs and audit reports belong in work/, not in metadata/.
"""

import argparse
from collections import defaultdict
import copy
import datetime
import fnmatch
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from urllib.parse import urljoin, urlsplit
import urllib.request
import xml.etree.ElementTree as ET

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from html import unescape
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
import urllib.request


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


class ProjectPage(HTMLParser):
    """Only itch.io's project description/gallery; never comment attachments."""
    def __init__(self, excluded_images=()):
        super().__init__()
        self.excluded_images = set(excluded_images)
        self.description_depth = 0
        self.capture = None
        self.text = []
        self.paragraphs = []
        self.images = []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            if self.description_depth:
                self.description_depth += 1
            elif "formatted_description" in attrs.get("class", "").split():
                self.description_depth = 1
        if self.description_depth and tag in ("p", "li"):
            self.capture, self.text = tag, []
        if tag == "a":
            self.anchor = attrs.get("href") if attrs.get("data-image_lightbox") == "true" else None
        if tag == "img" and "screenshot" in attrs.get("class", "").split() and attrs.get("data-screenshot_id"):
            if self.anchor and self.anchor not in self.excluded_images:
                self.images.append((self.anchor, "Application interface"))

    def handle_endtag(self, tag):
        if tag == self.capture:
            paragraph = " ".join("".join(self.text).split())
            if len(paragraph) >= 60 and paragraph not in self.paragraphs:
                self.paragraphs.append(paragraph)
            self.capture = None
        if tag == "div" and self.description_depth:
            self.description_depth -= 1
        if tag == "a":
            self.anchor = None

    def handle_data(self, data):
        if self.capture:
            self.text.append(data)


class ReadmeImages(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img" and attrs.get("src"):
            self.images.append((attrs["src"], attrs.get("alt", "")))


class WebsitePresentation(HTMLParser):
    """Extract explicit app descriptions and clearly labelled UI images."""
    def __init__(self):
        super().__init__()
        self.paragraphs = []
        self.images = []
        self.main_depth = 0
        self.capture = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").casefold()
            content = " ".join((attrs.get("content") or "").split())
            if key in ("description", "og:description", "twitter:description") and content:
                self.paragraphs.append(content)
        if tag in ("main", "article"):
            self.main_depth += 1
        if self.main_depth and tag == "p":
            self.capture, self.text = True, []
        if tag != "img":
            return
        src = attrs.get("src") or attrs.get("data-src") or attrs.get("data-lazy-src")
        label = " ".join((attrs.get(key) or "") for key in ("alt", "title", "class", "id", "data-caption"))
        filename = Path(urlsplit(src or "").path).name
        label += " " + filename
        if src and SCREENSHOT_LABEL.search(label) and not re.search(
                r"logo|badge|banner|shield|avatar|sponsor|donat", label, re.I):
            self.images.append((src, attrs.get("alt") or attrs.get("title") or "Application interface"))

    def handle_endtag(self, tag):
        if tag == "p" and self.capture:
            paragraph = " ".join("".join(self.text).split())
            if 120 <= len(paragraph) <= 1800:
                self.paragraphs.append(paragraph)
            self.capture = False
        if tag in ("main", "article") and self.main_depth:
            self.main_depth -= 1

    def handle_data(self, data):
        if self.capture:
            self.text.append(data)


SCREENSHOT_LABEL = re.compile(
    r"screenshot|screen[-_ ]?shot|preview|overview|interface|main[-_ ]?window|(?:^|[\W_])ui(?:[\W_]|$)",
    re.I,
)
WEBSITE_DOC_TEXT = re.compile(
    r"\b(?:this site contains|learn to build|life of a .* developer|"
    r"developer documentation|contributing code|checkout, build,? &? run)\b",
    re.I,
)


def readme_presentation(text):
    images = [(url, alt) for alt, url in re.findall(r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)', text)]
    references = {label.casefold(): url for label, url in re.findall(
        r"(?m)^\s*\[([^\]]+)\]:\s*<?([^\s>]+)>?(?:\s+['\"(].*?)?$", text)}
    for alt, reference in re.findall(r"!\[([^\]]*)\]\[([^\]]*)\]", text):
        url = references.get((reference or alt).casefold())
        if url:
            images.append((url, alt))
    html = ReadmeImages()
    html.feed(text)
    images.extend(html.images)
    screenshots, seen = [], set()
    for url, alt in images:
        if url in seen:
            continue
        seen.add(url)
        filename = Path(urlsplit(url).path).name
        label = filename + " " + alt
        if re.search(r"logo|badge|banner|shield|avatar|sponsor|donat", label, re.I):
            continue
        # READMEs often show the actual app UI under neutral names such as
        # main.png or modern.png. Keep those as candidates too; image() checks
        # dimensions and the resolver applies a stricter size/aspect filter to
        # candidates without a screenshot-like filename or caption.
        screenshots.append((url, alt or "Application interface"))
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph or paragraph.startswith(("#", "[!", "![", "<", ">", "|", "- ", "* ")):
            continue
        if re.match(r"(?:install|download|build|license|copyright|sponsor|donate|sudo|curl|wget)\b", paragraph, re.I):
            continue
        paragraph = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", paragraph)
        paragraph = unescape(re.sub(r"<[^>]+>|[`*_]", "", paragraph))
        paragraph = " ".join(paragraph.split())
        if 120 <= len(paragraph) <= 1800 and paragraph not in paragraphs:
            paragraphs.append(paragraph)
        if len(paragraphs) == 2:
            break
    return paragraphs, screenshots


class UpstreamResolver:
    def __init__(self, cache, workers=6):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.workers = workers
        self.lock = threading.Lock()
        self.url_locks = {}
        self.results = {}
        self.screenshot_sources = json.loads(
            (Path(__file__).resolve().parent / "metadata/screenshot_sources.json").read_text())

    def fetch(self, url, limit=512 * 1024):
        if urlsplit(url).scheme != "https":
            return None
        digest = hashlib.sha256(url.encode()).hexdigest()
        with self.lock:
            lock = self.url_locks.setdefault(digest, threading.Lock())
        with lock:
            body, info = self.cache / digest, self.cache / (digest + ".json")
            if info.is_file():
                metadata = json.loads(info.read_text())
                ttl = 86400 if metadata.get("ok") else 14400
                if time.time() - metadata["time"] < ttl:
                    return body.read_bytes() if metadata.get("ok") and body.is_file() else None
            metadata = {"url": url, "time": time.time(), "ok": False}
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "LunaOS-AppStream/1"})
                with urllib.request.urlopen(request, timeout=15) as stream:
                    data = stream.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Source exceeds size limit")
                body.write_bytes(data)
                metadata["ok"] = True
                return data
            except (HTTPError, URLError, OSError, ValueError) as error:
                metadata["error"] = str(error)
                return None
            finally:
                info.write_text(json.dumps(metadata) + "\n")

    def image(self, url, caption):
        data = self.fetch(url, limit=12 * 1024 * 1024)
        if data is None:
            return None
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                image.verify()
            if width < 480 or height < 270:
                return None
            return {"url": url, "caption": caption, "width": width, "height": height}
        except (OSError, ValueError, Image.DecompressionBombError):
            return None

    def supports(self, component, url, trusted_project_url=False):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https":
            return False
        if host not in ("github.com", "gitlab.com") and not host.endswith(".itch.io"):
            # Package databases often point directly to the project homepage.
            # Crawl it for every app; WebsitePresentation accepts only explicit
            # description metadata and images labelled as UI/screenshots.
            return bool(host and host not in ("archlinux.org", "aur.archlinux.org"))
        parts = parsed.path.strip("/").split("/")
        if host in ("github.com", "gitlab.com") and len(parts) < 2:
            return False
        if trusted_project_url:
            return bool(parts[0] and parts[1])
        project = parts[1] if host == "github.com" else parts[-1]
        if not project:
            return False
        identity = normalized(project.removesuffix(".git"))
        names = [normalized(c.text or "") for c in component.findall("name")]
        package = re.sub(r"-(?:bin|git|appimage)$", "", component.findtext("pkgname", ""))
        names.append(normalized(package))
        return any(identity == name or (len(identity) >= 6 and name.startswith(identity)) for name in names)

    def steam(self, component, project_url, steam_url=None):
        # A reviewed Steam URL is an explicit identity mapping, so it is safe
        # for non-game software too. Search by title only for games and keep
        # the stricter website corroboration on that unreviewed path.
        direct_url = steam_url or project_url
        direct = re.search(r"store\.steampowered\.com/app/(\d+)", direct_url)
        if not direct and component.find("categories/category[.='Game']") is None:
            return None
        names = [e.text or "" for e in component.findall("name") if not e.get("{http://www.w3.org/XML/1998/namespace}lang")]
        if not names or len(names[0]) < 4:
            return None
        if direct:
            ids = [direct[1]]
        else:
            query = urlencode({"term": names[0], "l": "english", "cc": "us"})
            content = self.fetch("https://store.steampowered.com/api/storesearch/?" + query)
            if content is None:
                return None
            candidates = json.loads(content).get("items", [])
            ids = [str(c["id"]) for c in candidates if normalized(c.get("name", "")) in {normalized(n) for n in names}]
            if len(ids) != 1:
                return None
        content = self.fetch("https://store.steampowered.com/api/appdetails?appids=" + ids[0])
        if content is None:
            return None
        details = json.loads(content).get(ids[0], {})
        if not details.get("success"):
            return None
        data = details["data"]
        if not direct:
            # Same titles can refer to unrelated games. Require the publisher's
            # website to corroborate the native package's project URL as well.
            def identity(url):
                p = urlsplit(url)
                return ((p.hostname or "").removeprefix("www.").lower(), p.path.rstrip("/").lower())
            if not data.get("website") or identity(data["website"]) != identity(project_url):
                return None
        images = []
        for screenshot in data.get("screenshots", [])[:4]:
            image = self.image(screenshot["path_full"], data.get("name", names[0]) + " screenshot")
            if image:
                images.append(image)
        if not images:
            return None
        return {"status": "found", "url": "https://store.steampowered.com/app/" + ids[0] + "/",
                "project_url": project_url, "paragraphs": [], "images": images}

    def resolve(self, component, url, trusted_project_url=False):
        package = component.findtext("pkgname", "")
        source = self.screenshot_sources.get(package, {})
        steam_url = source.get("url", "")
        has_steam_source = bool(re.search(r"store\.steampowered\.com/app/\d+", steam_url))
        if not self.supports(component, url, trusted_project_url) and not has_steam_source:
            return {"status": "no-supported-upstream", "url": url}
        parsed = urlsplit(url)
        source_url, paragraphs, candidates = url, [], []
        from_readme = False
        if (parsed.hostname or "").endswith(".itch.io"):
            content = self.fetch(url)
            if content:
                package = component.findtext("pkgname", "")
                exclusions = self.screenshot_sources.get(package, {}).get("exclude_images", [])
                page = ProjectPage(exclusions)
                page.feed(content.decode("utf-8", errors="replace"))
                paragraphs, candidates = page.paragraphs[:6], page.images
        elif parsed.hostname in ("github.com", "gitlab.com"):
            parts = parsed.path.strip("/").split("/")
            if parsed.hostname == "github.com":
                base = f"https://raw.githubusercontent.com/{parts[0]}/{parts[1].removesuffix('.git')}/HEAD/"
            else:
                base = url.rstrip("/").removesuffix(".git") + "/-/raw/HEAD/"
            for filename in ("README.md", "readme.md", "README.rst"):
                source_url = base + filename
                content = self.fetch(source_url)
                if content:
                    from_readme = True
                    paragraphs, candidates = readme_presentation(content.decode("utf-8", errors="replace"))
                    candidates = [(urljoin(base, image), caption) for image, caption in candidates]
                    break
        else:
            content = self.fetch(url)
            if content:
                page = WebsitePresentation()
                page.feed(content.decode("utf-8", errors="replace"))
                paragraphs = [p for p in page.paragraphs
                              if 120 <= len(p) <= 1800 and not WEBSITE_DOC_TEXT.search(p)][:2]
                candidates = [(urljoin(url, image), caption) for image, caption in page.images]
        images, seen = [], set()
        for candidate, caption in candidates:
            candidate = urljoin(url, candidate)
            if candidate in seen:
                continue
            seen.add(candidate)
            image = self.image(candidate, caption)
            label = Path(urlsplit(candidate).path).name + " " + caption
            if image and from_readme and not SCREENSHOT_LABEL.search(label) and (
                    image["width"] < 640 or image["width"] / image["height"] < 1.25):
                continue
            if image:
                images.append(image)
            if len(images) == 4:
                break
        if not images:
            steam = self.steam(component, url, steam_url if has_steam_source else None)
            if steam:
                steam["paragraphs"] = paragraphs
                return steam
        result = {"status": "found" if images or paragraphs else "upstream-has-no-presentation-data",
                  "url": source_url, "project_url": url, "paragraphs": paragraphs, "images": images}
        if trusted_project_url:
            result["url_source"] = "package-database"
        return result

    def resolve_many(self, jobs):
        pending = {key: value for key, value in jobs.items() if key not in self.results}
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {}
            for key, job in pending.items():
                component, url = job[:2]
                trusted_project_url = job[2] if len(job) > 2 else False
                futures[pool.submit(self.resolve, component, url, trusted_project_url)] = key
            for future in as_completed(futures):
                key = futures[future]
                try:
                    self.results[key] = future.result()
                except Exception as error:
                    self.results[key] = {"status": "upstream-check-failed", "error": str(error)}
        return {key: self.results[key] for key in jobs}


ROOT = Path(__file__).resolve().parent
LANG = "{http://www.w3.org/XML/1998/namespace}lang"
FLATHUB_URL = "https://dl.flathub.org/repo/appstream/x86_64/appstream.xml.gz"


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", value.lower().removesuffix(".desktop"))


def project_url(value):
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/").removesuffix(".git").lower()
    if not host or (not path and host in ("github.com", "gitlab.com", "sourceforge.net", "gnome.org", "kde.org")):
        return ""
    return host + path


def package_urls(database_dir):
    result = {}
    for path in database_dir.glob("*.db"):
        with tarfile.open(path, "r:*") as archive:
            for entry in archive:
                if not entry.isfile() or not entry.name.endswith("/desc"):
                    continue
                data = archive.extractfile(entry).read().decode()
                name = re.search(r"(?m)^%NAME%\n([^\n]+)", data)
                url = re.search(r"(?m)^%URL%\n([^\n]+)", data)
                if name and url:
                    result[name[1]] = url[1]
    return result


def description_length(component):
    description = next((e for e in component.findall("description") if not e.get(LANG)), None)
    return len(" ".join(description.itertext()).strip()) if description is not None else 0


def default_descriptions(component):
    return [description for description in component.findall("description") if not description.get(LANG)]


def missing_fields(component):
    result = []
    if description_length(component) < 160:
        result.append("description")
    if not has_screenshots(component):
        result.append("screenshots")
    if not component.findall("icon"):
        result.append("icons")
    return result


def has_screenshots(component):
    return any((image.text or "").strip().startswith(("https://", "http://"))
               for image in component.findall(".//screenshots/screenshot/image"))


def remove_flatpak_instructions(component):
    """Remove Flatpak-only caveats from metadata shown for native packages."""
    removed = False
    for description in component.findall("description"):
        original = copy.deepcopy(description)
        changed = False
        for parent in list(description.iter()):
            for child in list(parent):
                text = " ".join(child.itertext()).lower()
                if child.tag in ("p", "li") and any(word in text for word in ("flatpak", "flathub")):
                    parent.remove(child)
                    changed = True
        for child in list(description):
            if not "".join(child.itertext()).strip():
                description.remove(child)
        if changed and not "".join(description.itertext()).strip():
            component.remove(description)
            component.append(original)
        elif changed:
            removed = True
    return removed


def replace_presentation(target, source):
    for tag in ("description", "screenshots"):
        for element in target.findall(tag):
            target.remove(element)
        target.extend(copy.deepcopy(source.findall(tag)))


def apply_manual_screenshots(component, images):
    if not images:
        return False
    for old in component.findall("screenshots"):
        component.remove(old)
    screenshots = ET.SubElement(component, "screenshots")
    for index, image in enumerate(images):
        shot = ET.SubElement(screenshots, "screenshot", **({"type": "default"} if index == 0 else {}))
        if image.get("caption"):
            ET.SubElement(shot, "caption").text = image["caption"]
        ET.SubElement(shot, "image", type="source").text = image["url"]
    return True


def presentation(component):
    """Copy portable UI data, excluding Flatpak-specific instructions and thumbnails."""
    result = ET.Element("component", type="desktop-application")
    for source in component:
        if source.tag not in ("description", "screenshots"):
            continue
        element = copy.deepcopy(source)
        if element.tag == "description":
            for parent in list(element.iter()):
                for child in list(parent):
                    text = " ".join(child.itertext()).lower()
                    if child.tag in ("p", "li") and any(s in text for s in ("flatpak", "flathub")):
                        parent.remove(child)
            for child in list(element):
                if not "".join(child.itertext()).strip():
                    element.remove(child)
        else:
            for screenshot in list(element):
                for image in list(screenshot):
                    if image.tag in ("image", "video"):
                        if image.tag == "video" or image.get("type", "source") != "source":
                            screenshot.remove(image)
                        elif not (image.text or "").strip().startswith(("https://", "http://")):
                            screenshot.remove(image)
                        elif re.search(r"(?:^|[/_-])(?:banner|logo|icon|badge)(?:[/_.-]|$)", urlsplit(image.text).path, re.I):
                            screenshot.remove(image)
                        else:
                            image.text = image.text.strip()
                if not screenshot.findall("image"):
                    element.remove(screenshot)
            # Some upstream files put several full-size images in one screenshot.
            # AppStream permits only one source image per language per screenshot.
            for screenshot in list(element):
                seen = set()
                for image in list(screenshot.findall("image")):
                    language = image.get(LANG, "")
                    if language in seen:
                        screenshot.remove(image)
                        additional = ET.Element("screenshot")
                        additional.extend(copy.deepcopy(screenshot.findall("caption")))
                        additional.append(image)
                        element.append(additional)
                    seen.add(language)
            for index, screenshot in enumerate(element):
                screenshot.attrib.pop("type", None)
                if index == 0:
                    screenshot.set("type", "default")
        if len(element):
            result.append(element)
    # MetaInfo translates individual paragraphs/list items; catalogs translate
    # entire description sections. Convert local overrides before merging them.
    descriptions = {}
    for description in result.findall("description"):
        default_language = description.get(LANG, "")
        for child in description:
            language = child.get(LANG, default_language)
            if child.tag in ("ul", "ol"):
                lists = {}
                for item in child:
                    item_language = item.get(LANG, language)
                    translated_list = lists.setdefault(item_language, ET.Element(child.tag))
                    translated_list.append(copy.deepcopy(item))
                blocks = lists.items()
            else:
                blocks = [(language, copy.deepcopy(child))]
            for locale, block in blocks:
                for descendant in block.iter():
                    descendant.attrib.pop(LANG, None)
                section = descriptions.setdefault(locale, ET.Element("description", **({LANG: locale} if locale else {})))
                section.append(block)
        result.remove(description)
    result.extend(descriptions.values())
    return result


def volatile_screenshots(component):
    return any(re.match(r"https://raw\.githubusercontent\.com/[^/]+/[^/]+/(?:master|main|HEAD)/", image.text or "")
               for image in component.findall(".//screenshots/screenshot/image"))


def merge_presentation(native, supplement, replace_screenshots=False):
    changed = []
    data = presentation(supplement)
    incoming_descriptions = default_descriptions(data)
    if description_length(native) < 160 and incoming_descriptions and description_length(data) > description_length(native):
        for element in default_descriptions(native):
            native.remove(element)
        native.extend(copy.deepcopy(incoming_descriptions))
        changed.append("description")
    if (replace_screenshots or not has_screenshots(native)) and data.find("screenshots") is not None:
        for element in native.findall("screenshots"):
            native.remove(element)
        native.append(copy.deepcopy(data.find("screenshots")))
        changed.append("refreshed-screenshots" if replace_screenshots else "screenshots")
    return changed


def apply_enrichment(root, repo, package_names, directory=None):
    """Apply reviewed presentation fixes to this repository's own components."""
    if directory is None:
        return []
    sources_path = directory / "sources.json"
    sources = json.loads(sources_path.read_text()).get("components", {}) if sources_path.is_file() else {}
    applied = []
    path = directory / f"{repo}.xml"
    if not path.is_file():
        return applied
    by_id = {c.findtext("id"): c for c in root.findall("component")}
    for supplement in ET.parse(path).getroot().findall("component"):
        name, cid = supplement.findtext("pkgname"), supplement.findtext("id")
        if not name or not cid:
            raise ValueError(f"Missing package/component ID in {path}")
        native = by_id.get(cid)
        if native is None or name not in package_names or native.findtext("pkgname") != name:
            continue
        fields = sources.get(f"{repo}/{cid}", {}).get("fields", [])
        changed = merge_presentation(native, supplement, "refreshed-screenshots" in fields)
        if changed:
            applied.append({"repository": repo, "package": name, "id": cid, "fields": changed})
    return applied


def import_catalogs(catalog_dir, flathub_path, output, database_dir=Path("/var/lib/pacman/sync"),
                    icon_dir=None, project_urls=None, upstream_resolver=None, additional_catalogs=()):
    catalog_components, component_sources, catalog_sources = {}, {}, []
    for index, source_path in enumerate([flathub_path, *additional_catalogs]):
        payload = source_path.read_bytes()
        root = ET.fromstring(gzip.decompress(payload) if payload.startswith(b"\x1f\x8b") else payload)
        base = root.get("media_baseurl", "")
        origin = root.get("origin", "flatpak" if index == 0 else source_path.name.removesuffix(".xml.gz").removesuffix(".xml"))
        source_url = FLATHUB_URL if index == 0 else "https://appstream.debian.org/"
        if origin.startswith("debian-sid-"):
            source_url = f"https://appstream.debian.org/data/sid/{origin.removeprefix('debian-sid-')}/Components-amd64.yml.gz"
        provenance = source_path.with_name(source_path.name + ".source.json")
        if provenance.is_file():
            source_info = json.loads(provenance.read_text())
            source_url = source_info.get("archive_url", source_info["url"])
        catalog_sources.append({"origin": origin, "url": source_url, "sha256": hashlib.sha256(payload).hexdigest()})
        for component in root.findall("component"):
            if component.get("type") not in ("desktop-application", "desktop"):
                continue
            for image in component.findall(".//image"):
                if base and image.text and not image.text.startswith(("https://", "http://")):
                    image.text = urljoin(base.rstrip("/") + "/", image.text)
            cid = component.findtext("id", "")
            key = normalize(cid)
            if key in catalog_components:
                existing = catalog_components[key]
                if merge_presentation(existing, component):
                    component_sources[existing.findtext("id")]["additional_sources"].append(source_url)
            else:
                catalog_components[key] = component
                component_sources[cid] = {"catalog_url": source_url, "origin": origin, "additional_sources": []}
    indexes = {key: defaultdict(dict) for key in ("id", "desktop", "name", "url")}
    native_urls = package_urls(database_dir)
    native_urls.update(project_urls or {})
    icon_dir = icon_dir or catalog_dir.parent / "icons"
    local_sources = json.loads((ROOT / "metadata/sources.json").read_text())
    matches_path = ROOT / "metadata/matches.json"
    explicit_matches = json.loads(matches_path.read_text()) if matches_path.is_file() else {}
    screenshots_path = ROOT / "metadata/screenshot_sources.json"
    screenshot_sources = json.loads(screenshots_path.read_text()) if screenshots_path.is_file() else {}
    policy = json.loads((ROOT / "policy.json").read_text())
    for component in catalog_components.values():
        if component.get("type") not in ("desktop-application", "desktop"):
            continue
        cid = component.findtext("id", "")
        indexes["id"][normalize(cid)][cid] = component
        for element in component.findall("launchable"):
            if element.get("type") == "desktop-id":
                indexes["desktop"][normalize(element.text or "")][cid] = component
        for element in component.findall("name"):
            if not element.get(LANG):
                indexes["name"][normalize(element.text or "")][cid] = component
        for element in component.findall("url"):
            if element.get("type") in ("homepage", "vcs-browser"):
                url = project_url(element.text or "")
                if url:
                    indexes["url"][url][cid] = component
    output.mkdir(parents=True, exist_ok=True)
    audit, sources = [], {}
    for path in sorted(catalog_dir.glob("*.xml.gz")):
        repo = path.name.removesuffix(".xml.gz")
        if repo not in ("lunaos-repo", "chaotic-aur", "extra", "core", "multilib"):
            continue
        catalog = ET.fromstring(gzip.decompress(path.read_bytes()))
        full_catalog = copy.deepcopy(catalog)
        full_components = {c.findtext("id"): c for c in full_catalog.findall("component")}
        origin = catalog.get("origin", "")
        imported = ET.Element("components", version="1.0")
        jobs = {}
        if upstream_resolver is not None:
            for native in catalog.findall("component"):
                name, cid = native.findtext("pkgname"), native.findtext("id", "")
                if native.get("type") != "desktop-application" or not name or not missing_fields(native):
                    continue
                if (ROOT / "metadata" / name / "usr/share/metainfo").is_dir():
                    continue
                if name not in policy["keep_packages"] and any(fnmatch.fnmatchcase(name, p) for p in policy["exclude_packages"]):
                    continue
                if any(fnmatch.fnmatchcase(cid, p) for p in policy["exclude_components"]):
                    continue
                exact = indexes["id"].get(normalize(cid), {})
                if len(exact) == 1:
                    proposed = copy.deepcopy(native)
                    merge_presentation(proposed, next(iter(exact.values())))
                    if not missing_fields(proposed):
                        continue
                homepage_url = native.findtext("url[@type='homepage']") or ""
                package_url = native_urls.get(name, "")
                url, trusted_project_url = "", False
                for candidate_url, trusted in ((homepage_url, False), (package_url, True)):
                    if candidate_url and upstream_resolver.supports(native, candidate_url, trusted):
                        url, trusted_project_url = candidate_url, trusted
                        break
                has_reviewed_steam = bool(re.search(
                    r"store\.steampowered\.com/app/\d+",
                    screenshot_sources.get(name, {}).get("url", "")))
                if url or has_reviewed_steam:
                    jobs[cid] = (native, url, trusted_project_url)
            print(f"{repo}: checking {len(jobs)} upstream projects for missing presentation data", flush=True)
        upstream_results = upstream_resolver.resolve_many(jobs) if jobs else {}
        for native in catalog.findall("component"):
            if native.get("type") != "desktop-application" or not native.findtext("pkgname"):
                continue
            full_native = full_components.get(native.findtext("id", ""))
            flatpak_text_removed = remove_flatpak_instructions(native)
            if full_native is not None:
                remove_flatpak_instructions(full_native)
            missing = missing_fields(native)
            volatile = volatile_screenshots(native)
            name, cid = native.findtext("pkgname"), native.findtext("id", "")
            manual_screenshots = screenshot_sources.get(name, {})
            force_screenshot_refresh = bool(manual_screenshots.get("replace_screenshots"))
            if (name not in policy["keep_packages"] and any(fnmatch.fnmatchcase(name, p) for p in policy["exclude_packages"])) or any(fnmatch.fnmatchcase(cid, p) for p in policy["exclude_components"]):
                audit.append({"repository": repo, "package": name, "id": cid, "missing": missing, "status": "excluded-by-policy"})
                continue
            if not missing and not volatile and not flatpak_text_removed and not force_screenshot_refresh:
                audit.append({"repository": repo, "package": name, "id": cid, "missing": [], "status": "complete", "remaining": []})
                continue
            local_metadata = sorted((ROOT / "metadata" / name / "usr/share/metainfo").glob("*.xml"))
            candidates = dict(indexes["id"].get(normalize(cid), {}))
            for launchable in native.findall("launchable"):
                if launchable.get("type") == "desktop-id":
                    candidates.update(indexes["desktop"].get(normalize(launchable.text or ""), {}))
            reason = "component-or-desktop-id"
            if not candidates:
                for element in native.findall("name"):
                    if not element.get(LANG):
                        candidates.update(indexes["name"].get(normalize(element.text or ""), {}))
                base = normalize(re.sub(r"(-bin|-appimage)$", "", name))
                candidates = {key: value for key, value in candidates.items()
                              if len(base) >= 5 and base in (normalize(key.split(".")[-1]),
                                                           normalize(value.findtext("name", "")))}
                reason = "unique-name-and-package"
            if not candidates:
                urls = [e.text or "" for e in native.findall("url") if e.get("type") == "homepage"]
                urls.append(native_urls.get(name, ""))
                for url in urls:
                    candidates.update(indexes["url"].get(project_url(url), {}))
                # A homepage may belong to an entire suite (KDE, Maui, Simon
                # Tatham's games), or to both Community and Professional editions.
                # Corroborate the URL with the application name, not just the host.
                native_names = {normalize(e.text or "") for e in native.findall("name") if not e.get(LANG)}
                candidates = {key: value for key, value in candidates.items()
                              if native_names.intersection(normalize(e.text or "")
                                  for e in value.findall("name") if not e.get(LANG))}
                reason = "unique-project-url"
            if name == "discord-ptb":
                candidates = indexes["id"].get(normalize("com.discordapp.Discord"), {})
                reason = "explicit-discord-variant"
            if name in explicit_matches:
                candidates = indexes["id"].get(normalize(explicit_matches[name]), {})
                reason = "explicit-native-package"
            if len(local_metadata) == 1:
                local = ET.parse(local_metadata[0]).getroot()
                candidates = {local.findtext("id", cid): local}
                reason = "reviewed-native-metainfo"
            discovered = upstream_results.get(cid, {})
            if (manual_screenshots.get("images") and
                    (force_screenshot_refresh or (not has_screenshots(native) and
                     not any(has_screenshots(candidate) for candidate in candidates.values())))):
                source = manual_screenshots
                local = copy.deepcopy(native)
                if candidates:
                    merge_presentation(local, next(iter(candidates.values())))
                apply_manual_screenshots(local, source["images"])
                candidates = {cid: local}
                reason = "reviewed-upstream-screenshots"
            record = {"repository": repo, "package": name, "id": cid, "missing": missing}
            if discovered:
                record["upstream_check"] = {key: value for key, value in discovered.items() if key not in ("paragraphs", "images")}
            if discovered.get("status") == "found":
                # First retain the catalog match, then fill its remaining gaps
                # from the package's own project rather than another app's data.
                local = copy.deepcopy(native)
                if len(candidates) == 1:
                    merge_presentation(local, next(iter(candidates.values())))
                found = ET.Element("component", type="desktop-application")
                description = ET.SubElement(found, "description")
                for paragraph in discovered["paragraphs"]:
                    ET.SubElement(description, "p").text = paragraph
                screenshots = ET.SubElement(found, "screenshots")
                for index, image in enumerate(discovered["images"]):
                    shot = ET.SubElement(screenshots, "screenshot", **({"type": "default"} if index == 0 else {}))
                    ET.SubElement(shot, "caption").text = image["caption"][:120]
                    ET.SubElement(shot, "image", type="source", width=str(image["width"]), height=str(image["height"])).text = image["url"]
                if merge_presentation(local, found):
                    candidates = {cid: local}
                    reason = "verified-project-presentation"
            if len(candidates) != 1:
                record["status"] = "needs-upstream-review"
                audit.append(record)
                continue
            flat_id, upstream = next(iter(candidates.items()))
            supplement = copy.deepcopy(native)
            # Prefer a snapshot's mirrored screenshots over URLs tied to a moving
            # GitHub branch, which may have been renamed since a package release.
            mirrored = upstream.findall(".//screenshots/screenshot/image")
            refresh = (force_screenshot_refresh or
                       (volatile and any((image.text or "").startswith("https://dl.flathub.org/media/")
                                         for image in mirrored)))
            changed = merge_presentation(supplement, upstream, refresh)
            if flatpak_text_removed:
                changed.insert(0, "removed-flatpak-specific-description")
            record.update(source_id=flat_id, match=reason, added=changed)
            if not changed:
                record["status"] = "source-has-no-additional-data"
                audit.append(record)
                continue
            if full_native is not None:
                replace_presentation(full_native, supplement)
            if repo in ("extra", "core", "multilib"):
                if any(re.search(r"https?://", element.text or "") for element in supplement.findall("summary")):
                    for element in supplement.findall("summary"):
                        supplement.remove(element)
                    supplement.extend(copy.deepcopy(upstream.findall("summary")))
                clean_screenshots = presentation(supplement).find("screenshots")
                for screenshots in supplement.findall("screenshots"):
                    supplement.remove(screenshots)
                if clean_screenshots is not None:
                    supplement.append(clean_screenshots)
                # Reference components are appended to a different catalog origin.
                # Bundle their native icons under that origin as well.
                cached = []
                for icon in supplement.findall("icon"):
                    if icon.get("type") != "cached":
                        continue
                    size = f"{icon.get('width', '64')}x{icon.get('height', '64')}"
                    if icon.get("scale", "1") != "1":
                        size += "@" + icon.get("scale")
                    filename = icon.text or ""
                    if Path(filename).name != filename:
                        raise ValueError(f"Unsafe icon filename: {filename}")
                    source = icon_dir / origin / size / filename
                    if source.is_file():
                        filename = f"{repo}_{filename}"
                        target = output / "icons" / size / filename
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
                        icon.text = filename
                        cached.append(icon)
                    else:
                        supplement.remove(icon)
                if not any(i.get("height") == "64" and i.get("scale", "1") == "1" for i in cached):
                    record["status"] = "needs-native-icon"
                    audit.append(record)
                    continue
            # Drop old timestamps and releases from reference snapshots: native
            # package versions continue to come from the live pacman database.
            for release in supplement.findall("releases"):
                supplement.remove(release)
            imported.append(supplement)
            record["status"] = "imported"
            record["remaining"] = missing_fields(supplement)
            audit.append(record)
            sources[f"{repo}/{cid}"] = {"package": name, "source_id": flat_id,
                "url": f"https://flathub.org/apps/{flat_id.removesuffix('.desktop')}",
                "catalog_url": FLATHUB_URL, "match": reason, "fields": changed,
                "licenses": "Upstream metadata and media retain their respective licenses."}
            if flat_id in component_sources:
                provider = component_sources[flat_id]
                sources[f"{repo}/{cid}"].update(provider)
                if provider["origin"] != "flatpak":
                    sources[f"{repo}/{cid}"]["url"] = provider["catalog_url"]
            if reason == "reviewed-native-metainfo":
                sources[f"{repo}/{cid}"].update(local_sources.get(name, {}))
                sources[f"{repo}/{cid}"].pop("catalog_url", None)
            elif reason == "reviewed-upstream-screenshots":
                sources[f"{repo}/{cid}"].update({key: value for key, value in screenshot_sources[name].items() if key != "images"})
                sources[f"{repo}/{cid}"].pop("catalog_url", None)
            elif reason == "verified-project-presentation":
                sources[f"{repo}/{cid}"].update(url=discovered["url"], project_url=discovered["project_url"],
                                               credit="Upstream project contributors")
                sources[f"{repo}/{cid}"].pop("catalog_url", None)
        ET.indent(imported, space="  ")
        ET.ElementTree(imported).write(output / f"{repo}.xml", encoding="utf-8", xml_declaration=True)
        if repo in ("extra", "core", "multilib"):
            full_catalog.set("time", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            with (output / f"{repo}-enriched.xml.gz").open("wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
                    ET.ElementTree(full_catalog).write(zipped, encoding="utf-8", xml_declaration=True)
    sources_doc = {"catalog_sha256": hashlib.sha256(flathub_path.read_bytes()).hexdigest(), "catalogs": catalog_sources, "components": sources}
    (output / "sources.json").write_text(json.dumps(sources_doc, indent=2) + "\n")
    (output / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    summary = {"components": len(audit), "statuses": {}, "remaining": {}}
    counts = defaultdict(int)
    for record in audit:
        counts[record["status"]] += 1
        if record["status"] != "excluded-by-policy":
            for field in record.get("remaining", record["missing"]):
                summary["remaining"][field] = summary["remaining"].get(field, 0) + 1
    summary["statuses"] = dict(counts)
    (output / "quality.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(dict(counts), indent=2))


def rpm_catalog_member(names):
    candidates = [name for name in names if
                  ("/swcatalog/xml/" in "/" + name or "/app-info/xmls/" in "/" + name)
                  and name.endswith((".xml", ".xml.gz"))]
    if len(candidates) != 1:
        raise ValueError(f"Expected one RPM AppStream catalog, found {candidates}")
    return candidates[0]


def rpm_catalog_url(source, listing):
    pattern = re.escape(source["package_prefix"]) + r"[A-Za-z0-9_.+~%-]+\.noarch\.rpm"
    names = set(re.findall(pattern, listing))
    if len(names) != 1:
        raise ValueError(f"Expected one current AppStream RPM, found {sorted(names)}")
    return urljoin(source["url"], names.pop())


def html_links(document):
    return re.findall(r"href=[\"']([^\"']+)[\"']", document, re.I)


def fedora_rpm_url(source, fetch_listing):
    root = source["url"].rstrip("/") + "/"
    versions = sorted({name.strip("/") for name in html_links(fetch_listing(root))
                       if re.fullmatch(r"\d+/?", name)})
    if not versions:
        raise ValueError("No Fedora appstream-data builds found in Koji")
    version = versions[-1]
    version_url = urljoin(root, version + "/")
    releases = []
    for name in html_links(fetch_listing(version_url)):
        match = re.fullmatch(r"(\d+)\.fc(\d+)/?", name)
        if match:
            releases.append((int(match[2]), int(match[1]), match[1] + ".fc" + match[2]))
    if not releases:
        raise ValueError(f"No Rawhide build found for Fedora appstream-data {version}")
    release = max(releases)[2]
    arch_url = urljoin(version_url, release + "/noarch/")
    expected = rf"appstream-data-{re.escape(version)}-{re.escape(release)}\.noarch\.rpm"
    rpm_names = sorted({name for name in html_links(fetch_listing(arch_url))
                        if re.fullmatch(expected, name)})
    if len(rpm_names) != 1:
        raise ValueError(f"Expected one Fedora Rawhide appstream-data RPM, found {rpm_names}")
    return urljoin(arch_url, rpm_names[0])


def download_catalogs(directory):
    """Refresh the configured distribution databases, retaining optional cached sources."""
    directory.mkdir(parents=True, exist_ok=True)
    loaded, report = [], []
    for source in json.loads((ROOT / "catalog_sources.json").read_text()):
        destination = directory / (source["name"] + ".xml.gz")
        incoming = directory / (source["name"] + ".input." + ("yml.gz" if source["format"] == "yaml" else "xml.gz"))
        converted = directory / (source["name"] + ".converted.xml.gz")
        record = {"name": source["name"], "url": source["url"]}
        try:
            download_url = source["url"]
            if source["format"] == "rpm":
                request = urllib.request.Request(download_url, headers={"User-Agent": "LunaOS-AppStream/1"})
                with urllib.request.urlopen(request, timeout=90) as stream:
                    download_url = rpm_catalog_url(source, stream.read().decode("utf-8"))
                record["archive_url"] = download_url
            elif source["format"] == "fedora-rpm":
                def fetch_listing(url):
                    request = urllib.request.Request(url, headers={"User-Agent": "LunaOS-AppStream/1"})
                    with urllib.request.urlopen(request, timeout=90) as stream:
                        return stream.read().decode("utf-8")
                download_url = fedora_rpm_url(source, fetch_listing)
                record["archive_url"] = download_url
            request = urllib.request.Request(download_url, headers={"User-Agent": "LunaOS-AppStream/1"})
            with urllib.request.urlopen(request, timeout=180) as stream, incoming.open("wb") as out:
                shutil.copyfileobj(stream, out)
            if source["format"] in ("rpm", "fedora-rpm"):
                names = subprocess.check_output(["bsdtar", "-tf", str(incoming)], text=True).splitlines()
                if source["format"] == "fedora-rpm":
                    member = next((name for name in names if name.endswith("/usr/share/swcatalog/xml/fedora.xml.gz")), "")
                    if not member:
                        raise ValueError("Fedora RPM does not contain fedora.xml.gz")
                else:
                    member = rpm_catalog_member(names)
                payload = subprocess.check_output(["bsdtar", "-xOf", str(incoming), member])
                converted.write_bytes(payload if payload.startswith(b"\x1f\x8b") else gzip.compress(payload, mtime=0))
                record["member"] = member
                record["archive_sha256"] = hashlib.sha256(incoming.read_bytes()).hexdigest()
            elif source["format"] == "yaml":
                subprocess.run(["appstreamcli", "convert", str(incoming), str(converted)], check=True,
                               capture_output=True, text=True)
            else:
                shutil.copyfile(incoming, converted)
            root = ET.fromstring(gzip.decompress(converted.read_bytes()))
            if root.tag != "components" or root.find("component") is None:
                raise ValueError("Expected a nonempty AppStream catalog")
            converted.replace(destination)
            record["status"] = "downloaded"
            destination.with_name(destination.name + ".source.json").write_text(json.dumps(record, indent=2) + "\n")
        except (OSError, ValueError, subprocess.CalledProcessError, ET.ParseError) as error:
            record.update(status="cached" if destination.is_file() else "unavailable", error=str(error))
            if source["required"] and not destination.is_file():
                raise
            print(f"Catalog source {source['name']}: {record['status']}: {error}", flush=True)
        finally:
            incoming.unlink(missing_ok=True)
            converted.unlink(missing_ok=True)
            report.append(record)
        if destination.is_file():
            loaded.append(destination)
    (directory / "status.json").write_text(json.dumps(report, indent=2) + "\n")
    if not loaded:
        raise ValueError("No upstream catalogs available")
    return loaded[0], loaded[1:]


def prepare_enrichment(repo, native_catalog, directory, flathub_path, database_dir,
                       project_urls=None, reference_root=Path("/usr/share/swcatalog"), offline=False,
                       additional_catalogs=()):
    inputs = directory / "input"
    inputs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(native_catalog, inputs / f"{repo}.xml.gz")
    if repo == "lunaos-repo":
        for reference in ("extra", "core", "multilib"):
            source = reference_root / "xml" / f"{reference}.xml.gz"
            if not source.is_file():
                raise ValueError(f"Missing reference catalog {source}; install archlinux-appstream-data")
            shutil.copyfile(source, inputs / source.name)
    output = directory / "catalogs"
    import_catalogs(inputs, flathub_path, output, database_dir,
                    reference_root / "icons", project_urls,
                    None if offline else UpstreamResolver(directory.parent / "upstream-cache"), additional_catalogs)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", type=Path, default=Path("/usr/share/swcatalog/xml"))
    parser.add_argument("--flathub-catalog", type=Path, help="Use a local snapshot for an offline import")
    parser.add_argument("--additional-catalog", type=Path, action="append", default=[], help="Additional AppStream XML snapshots")
    parser.add_argument("--output", type=Path, default=ROOT / "work/enrichment/catalogs")
    parser.add_argument("--database-dir", type=Path, default=Path("/var/lib/pacman/sync"))
    parser.add_argument("--offline", action="store_true", help="Disable upstream discovery; requires a local catalog snapshot")
    args = parser.parse_args()
    if args.offline and args.flathub_catalog is None:
        parser.error("--offline requires --flathub-catalog")
    if args.flathub_catalog is None:
        args.flathub_catalog, downloaded = download_catalogs(args.output.parent / "sources")
        args.additional_catalog.extend(downloaded)
    import_catalogs(args.catalog_dir, args.flathub_catalog, args.output, args.database_dir,
                    upstream_resolver=None if args.offline else UpstreamResolver(args.output.parent / "upstream-cache"),
                    additional_catalogs=args.additional_catalog)


if __name__ == "__main__":
    main()
