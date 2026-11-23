#!/usr/bin/env python3
"""Build the website from the Markdown protocols (needs pandoc >= 3.1).

usage: python3 tools/build.py [md/NNN-name.md ...]

    md/NNN-name.md   ->  docs/NNN-name.html      one page per protocol
    all protocols    ->  index.html              home page (catalogue)
                         docs/search.html        search page
                         docs/search_index.json  full-text search index

Without argument, every page is rebuilt. With Markdown files as arguments, only their pages are
rebuilt (plus the home page and the search index): enough after editing the text of a protocol,
but rebuild everything after adding a protocol or changing a title or a category (the left menu
of every page lists all the protocols).

Front matter of a protocol (YAML header of the Markdown file):
    title, protocol (number), revision, date (last update), category, source (original docx),
    draft: true (flagged as draft), numbersections: false (no section numbers),
    public: true (also published on the public website, see tools/export_public.py).

Settings of this copy of the website, in site.json (optional): repo_url (link to the repository),
file_url (start of the links to its files, e.g. .../blob/main), subtitle (under the site title),
public_only (true in the public repository: protocols without `public: true` are left out).
"""

import argparse
import concurrent.futures
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD_DIR = "md"
OUT_DIR = "docs"
TEMPLATE = "tools/template.html"
FILTER = "tools/filters/site.lua"
SITE_TITLE = "Bioinformatics protocols"
SETTINGS_FILE = "site.json"
# used when site.json is absent: the public repository on GitHub
DEFAULT_SETTINGS = {
    "repo_url": "https://github.com/gael-millot/protocols",
    "file_url": "https://github.com/gael-millot/protocols/blob/main",
    "subtitle": "gael-millot/protocols",
    "public_only": False,
}
MATHJAX = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml-full.js"
# no autolink_bare_uris: it turns "example:\\" or "info:x" into links; write URLs as <https://...>
READER = "markdown-smart-citations-raw_tex"
GENERATOR = 'content="pandoc (tools/build.py)"'
# order of the categories in the menu and on the home page (unknown categories go at the end)
CATEGORIES = [
    "Linux, shells and editors",
    "R, Python and Java",
    "Clusters, containers and workflows",
    "Git, documentation and publishing",
    "Sequencing data and formats",
    "Genomics and genetics",
    "SARS-CoV-2 genomic surveillance",
    "Immunology and single cell",
    "Statistics and machine learning",
    "AI and large language models",
    "Windows, devices and setup",
    "Office, graphics and media",
    "Communication, file transfer and IT",
    "Other",
]
PANDOC_CANDIDATES = [
    "/mnt/c/Program Files/RStudio/resources/app/bin/quarto/bin/tools/pandoc.exe",
    r"C:\Program Files\RStudio\resources\app\bin\quarto\bin\tools\pandoc.exe",
]


def find_pandoc(arg):
    for c in [arg, os.environ.get("PANDOC"), shutil.which("pandoc")] + PANDOC_CANDIDATES:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    sys.exit("pandoc not found: install it (sudo apt install pandoc) or use --pandoc")


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    path = os.path.join(REPO, SETTINGS_FILE)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                settings.update(json.load(f))
        except ValueError as e:
            sys.exit("%s: %s" % (SETTINGS_FILE, e))
    for key in ("repo_url", "file_url"):
        settings[key] = settings[key].rstrip("/")
    return settings


SETTINGS = load_settings()


def is_true(value):
    return str(value).strip().lower() in ("true", "yes")


def front_matter(path):
    """Scalar keys of the YAML header (enough for the keys used by the site)."""
    with open(path, encoding="utf-8") as f:
        text = f.read(20000)
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)", text, re.S)
    meta = {}
    if not m:
        return meta
    for line in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
        if not mm:
            continue
        key, val = mm.group(1), mm.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1].replace("''", "'") if val[0] == "'" else val[1:-1].replace('\\"', '"')
        meta[key] = val
    return meta


def display_num(num):
    return str(int(num)) if num.isdigit() else num


def load_pages():
    pages = []
    for fn in sorted(os.listdir(os.path.join(REPO, MD_DIR))):
        if not fn.endswith(".md") or fn.startswith(("_", ".")):
            continue
        meta = front_matter(os.path.join(REPO, MD_DIR, fn))
        num = str(meta.get("protocol", "")).strip()
        pages.append({
            "slug": fn[:-3],
            "title": meta.get("title") or fn[:-3],
            "num": num,
            "category": meta.get("category") or "Other",
            "date": meta.get("date", ""),
            "revision": meta.get("revision", ""),
            "draft": is_true(meta.get("draft", "")),
            "public": is_true(meta.get("public", "")),
        })
    return pages


def group_pages(pages):
    order = {c: i for i, c in enumerate(CATEGORIES)}
    cats = {}
    for p in pages:
        cats.setdefault(p["category"], []).append(p)
    for items in cats.values():
        items.sort(key=lambda p: (int(p["num"]) if p["num"].isdigit() else 10 ** 6, p["slug"]))
    return sorted(cats.items(), key=lambda kv: (order.get(kv[0], len(CATEGORIES)), kv[0]))


def nav_meta(groups, current_slug, href_prefix):
    nav = []
    for cat, items in groups:
        entry = {"name": cat, "items": []}
        for p in items:
            item = {"href": href_prefix + p["slug"] + ".html", "label": p["title"], "num": display_num(p["num"])}
            if p["slug"] == current_slug:
                item["current"] = True
                entry["current"] = True
            entry["items"].append(item)
        nav.append(entry)
    return nav


def run_pandoc(pandoc, args):
    r = subprocess.run([pandoc] + args, cwd=REPO, capture_output=True, text=True)
    msg = "\n".join(l for l in (r.stderr or "").splitlines() if l.strip())
    return r.returncode, msg


def common_vars(root):
    icon = "gitlab" if "gitlab" in SETTINGS["repo_url"] else "github"
    return ["-V", "root=" + root, "-V", "repo_url=" + SETTINGS["repo_url"], "-V", "file_url=" + SETTINGS["file_url"],
            "-V", "repo_icon=" + icon, "-V", "sitetitle=" + SITE_TITLE, "-V", "sitesubtitle=" + SETTINGS["subtitle"]]


def build_page(pandoc, page, groups, order, tmpdir):
    slug = page["slug"]
    i = order.index(slug)
    meta = {
        "nav": nav_meta(groups, slug, ""),
        "imgprefix": "../" + MD_DIR + "/",
        "pagetitle": "%s · protocol %s · %s" % (page["title"], display_num(page["num"]), SITE_TITLE)
        if page["num"] else "%s · %s" % (page["title"], SITE_TITLE),
        "category": page["category"],
    }
    if page["public"] and not SETTINGS["public_only"]:
        meta["publicflag"] = True
    by_slug = {p["slug"]: p for _, items in groups for p in items}
    if i > 0:
        meta["prev"] = {"href": order[i - 1] + ".html", "label": by_slug[order[i - 1]]["title"]}
    if i < len(order) - 1:
        meta["next"] = {"href": order[i + 1] + ".html", "label": by_slug[order[i + 1]]["title"]}
    meta_file = os.path.join(tmpdir, slug + ".json")
    with open(os.path.join(REPO, meta_file), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    args = ["%s/%s.md" % (MD_DIR, slug), "-f", READER, "-t", "html5", "-s",
            "--template=" + TEMPLATE, "--lua-filter=" + FILTER, "--metadata-file=" + meta_file,
            "--mathjax=" + MATHJAX,
            "-V", "source_url=%s/%s/%s.md" % (SETTINGS["file_url"], MD_DIR, slug),
            "-o", "%s/%s.html" % (OUT_DIR, slug)] + common_vars("../")
    code, msg = run_pandoc(pandoc, args)
    return slug, code, msg


def build_special(pandoc, groups, tmpdir, name, title, body_html, root, href_prefix, out, extra_meta=None):
    meta = {"nav": nav_meta(groups, None, href_prefix), "pagetitle": title}
    meta.update(extra_meta or {})
    meta_file = os.path.join(tmpdir, name + ".json")
    with open(os.path.join(REPO, meta_file), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    src = os.path.join(tmpdir, name + ".md")
    with open(os.path.join(REPO, src), "w", encoding="utf-8") as f:
        f.write("---\ntitle: %s\n---\n\n```{=html}\n%s\n```\n" % (json.dumps(title), body_html))
    args = [src, "-f", READER, "-t", "html5", "-s", "--template=" + TEMPLATE,
            "--metadata-file=" + meta_file, "-o", out] + common_vars(root)
    return run_pandoc(pandoc, args)


def home_body(groups, pages):
    e = html.escape
    n = len(pages)
    show_public = not SETTINGS["public_only"]
    parts = [
        '<p class="home-intro">Bioinformatics notes and procedures of '
        '<a href="https://github.com/gael-millot">Gael A. Millot</a> (Bioinformatics and Biostatistics Hub, '
        'Institut Pasteur, Paris), first written in MS Word. Each protocol is a Markdown file of the '
        '<a href="%s">repository</a> (folder <code>md/</code>).</p>' % SETTINGS["repo_url"],
        '<div class="home-tools"><input type="search" id="proto-filter" placeholder="Filter by name, number or topic" '
        'aria-label="Filter the protocols" autocomplete="off">'
        '<span class="home-count" id="proto-count">%d protocols</span>'
        '<a href="docs/search.html">Full-text search</a></div>' % n,
        '<p class="no-match" id="no-match" hidden>No protocol matches this filter. '
        'Try the <a href="docs/search.html">full-text search</a>.</p>',
    ]
    for cat, items in groups:
        parts.append('<section class="catalog-cat"><h2>%s<span class="cat-count">%d</span></h2>'
                     '<table class="catalog"><tbody>' % (e(cat), len(items)))
        for p in items:
            search = " ".join([display_num(p["num"]), p["num"], p["title"], p["slug"], cat]).lower()
            flags = '<span class="draft-flag">draft</span>' if p["draft"] else ""
            if show_public and p["public"]:
                flags += '<span class="public-flag">public</span>'
                search += " public"
            search = re.sub(r"\s+", " ", search)
            parts.append(
                '<tr data-search="%s"><td class="num">%s</td><td><a href="docs/%s.html">%s</a>%s</td>'
                '<td class="date">%s</td></tr>' % (
                    e(strip_accents(search)), e(display_num(p["num"])), e(p["slug"]), e(p["title"]),
                    flags, e(p["date"])))
        parts.append("</tbody></table></section>")
    return "\n".join(parts)


def strip_accents(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


SEARCH_BODY = """<form class="search-form" id="search-form" action="search.html" method="get" role="search">
<input type="search" id="search-input" name="q" placeholder="Words to find, e.g. sbatch array" aria-label="Search">
<button type="submit" class="btn btn-neutral">Search</button>
</form>
<p class="search-summary" id="search-summary" aria-live="polite"></p>
<ul class="search-results" id="search-results"></ul>"""


class SectionParser(HTMLParser):
    """Text of a built page, split by sections (h1-h4 with an id)."""

    BLOCK = {"p", "li", "td", "th", "tr", "pre", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
             "blockquote", "figcaption", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = [["", "", []]]
        self.in_head = None
        self.skip = 0
        self.stack = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        skip = tag in ("script", "style") or "header-section-number" in cls or "headerlink" in cls
        self.stack.append((tag, skip))
        if skip:
            self.skip += 1
            return
        if tag in ("h2", "h3", "h4") and a.get("id"):
            self.sections.append([a["id"], "", []])
            self.in_head = tag
        elif tag in self.BLOCK:
            self.sections[-1][2].append(" ")

    def handle_endtag(self, tag):
        while self.stack:
            t, skip = self.stack.pop()
            if skip:
                self.skip -= 1
            if t == tag:
                break
        if tag == self.in_head:
            self.in_head = None
        elif tag in self.BLOCK:
            self.sections[-1][2].append(" ")

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_head:
            self.sections[-1][1] += data
        else:
            self.sections[-1][2].append(data)


def search_entry(page):
    path = os.path.join(REPO, OUT_DIR, page["slug"] + ".html")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        content = f.read()
    a = content.find("<!--content-start-->")
    b = content.find("<!--content-end-->")
    if a < 0 or b < 0:
        return None
    p = SectionParser()
    p.feed(content[a:b])
    secs = []
    for sid, head, chunks in p.sections:
        text = re.sub(r"\s+", " ", "".join(chunks)).strip()
        head = re.sub(r"\s+", " ", head).strip()
        if sid == "":
            text = re.sub(r"^.*?Protocol \d+ revision \S+( last update [\d-]+)?( draft)?( public)?\s*", "",
                          text.replace(page["title"], "", 1).strip())
        if text or head:
            secs.append([sid, head, text])
    return {"u": page["slug"] + ".html", "t": page["title"], "n": display_num(page["num"]), "s": secs}


def clean_stale(pages):
    """Remove pages generated by this script whose Markdown file no longer exists."""
    keep = {p["slug"] + ".html" for p in pages} | {"search.html"}
    removed = []
    out = os.path.join(REPO, OUT_DIR)
    for fn in os.listdir(out):
        if fn.endswith(".html") and fn not in keep:
            path = os.path.join(out, fn)
            with open(path, encoding="utf-8", errors="replace") as f:
                head = f.read(600)
            if GENERATOR in head:
                os.remove(path)
                removed.append(fn)
    return removed


PROTOCOL_LINK = re.compile(r"\]\((\d{3}-[A-Za-z0-9_-]+)\.md(?:#[^)\s]*)?\)")


def dangling_links(pages, slugs):
    """(page, target) for the links to protocols (NNN-name.md) that are not on the website."""
    out = []
    for p in pages:
        with open(os.path.join(REPO, MD_DIR, p["slug"] + ".md"), encoding="utf-8") as f:
            targets = set(PROTOCOL_LINK.findall(f.read()))
        out.extend((p["slug"], t) for t in sorted(targets - slugs))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="Markdown files to rebuild (default: all)")
    ap.add_argument("--pandoc", help="pandoc executable (default: $PANDOC or pandoc on the PATH)")
    ap.add_argument("-j", "--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2))))
    ap.add_argument("--no-index", action="store_true",
                    help="only build the given pages (not the home page, search page and index)")
    a = ap.parse_args()
    pandoc = find_pandoc(a.pandoc)
    pages = load_pages()
    public_only = SETTINGS["public_only"]
    if public_only:
        pages = [p for p in pages if p["public"]]
    if not pages:
        sys.exit("no Markdown protocol in %s/%s" % (MD_DIR, " with public: true" if public_only else ""))
    groups = group_pages(pages)
    order = [p["slug"] for _, items in groups for p in items]
    if a.files:
        wanted = {os.path.splitext(os.path.basename(f))[0] for f in a.files}
        unknown = wanted - set(order)
        if unknown:
            sys.exit("not in %s/%s: %s" % (MD_DIR, " (or not public)" if public_only else "", ", ".join(sorted(unknown))))
        todo = [p for p in pages if p["slug"] in wanted]
    else:
        todo = pages
    for slug, target in dangling_links(todo, set(order)):
        print("WARNING %s.md links to %s.md, which is not on this website" % (slug, target), file=sys.stderr)
    os.makedirs(os.path.join(REPO, OUT_DIR), exist_ok=True)
    tmpdir = os.path.relpath(tempfile.mkdtemp(prefix=".build_", dir=REPO), REPO)
    errors = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futures = [ex.submit(build_page, pandoc, p, groups, order, tmpdir) for p in todo]
            for fut in concurrent.futures.as_completed(futures):
                slug, code, msg = fut.result()
                if code != 0:
                    errors += 1
                    print("ERROR %s:\n%s" % (slug, msg), file=sys.stderr)
                elif msg:
                    print("%s: %s" % (slug, msg), file=sys.stderr)
        if a.no_index:
            print("%d page(s) built, %d error(s)" % (len(todo) - errors, errors))
            sys.exit(1 if errors else 0)
        code, msg = build_special(pandoc, groups, tmpdir, "home", SITE_TITLE, home_body(groups, pages),
                                  "./", "docs/", "index.html")
        if code:
            errors += 1
            print("ERROR index.html:\n" + msg, file=sys.stderr)
        code, msg = build_special(pandoc, groups, tmpdir, "search", "Search", SEARCH_BODY, "../", "",
                                  OUT_DIR + "/search.html", {"script": ["search.js"]})
        if code:
            errors += 1
            print("ERROR search.html:\n" + msg, file=sys.stderr)
    finally:
        shutil.rmtree(os.path.join(REPO, tmpdir), ignore_errors=True)
    index = [e for e in (search_entry(p) for p in pages) if e]
    with open(os.path.join(REPO, OUT_DIR, "search_index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    removed = clean_stale(pages) if not a.files else []
    print("%d page(s) built, %d error(s); index.html, %s/search.html and search_index.json updated%s%s" % (
        len(todo) - errors, errors, OUT_DIR, "; removed: " + ", ".join(removed) if removed else "",
        " (public website: only the protocols with public: true)" if public_only else ""))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
