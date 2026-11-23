#!/usr/bin/env python3
"""Copy the public protocols to the local copy of the public repository and rebuild its website.

usage: python3 tools/export_public.py [DEST] [--pandoc PANDOC]

A protocol is public when its YAML header has `public: true`; every other protocol stays private.
DEST is the folder of the public repository (default: "dest" of "public_export" in site.json,
i.e. ../protocols-public). It receives:
    md/                the public protocols, where the redactions of public_redactions/redactions.json
                       are applied (values -> [REDACTED], screenshots removed or replaced by their
                       masked copy from public_redactions/img/), links to protocols that are not public
                       become plain text, with only the images they show
    docs/, index.html  the website, rebuilt there with tools/build.py
    docs/assets/, tools/, Licence.txt, .nojekyll, .gitignore   copied unchanged
    docs/Protocol ...  former page addresses of the public protocols
    README.md          without its private parts (lines between <!-- private --> and <!-- /private -->)
    site.json          settings of the public repository ("public_export" of site.json)
md/, docs/ and tools/ of DEST are replaced; its other files and its .git are left alone. Nothing is
committed or pushed. The export stops, and removes what it wrote, if DEST shares git history with
this repository, if a redaction of a public protocol no longer matches its text, or if the result
still contains a redacted value, a token-like string or the name of a private protocol.

public_redactions/redactions.json:
    {"text": [{"protocol": "044-bash", "find": "secret", "replace": "[REDACTED]"}, ...],
     "images": [{"protocol": "043-x", "image": "img/043-x/image20.png", "action": "remove" or "mask"}]}
A "find" is replaced in every public protocol; a "find" of a public protocol that matches nothing
there is an error (its text changed: update the entry). "mask" uses public_redactions/img/<slug>/<file>.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402  (tools/build.py)

REPO = build.REPO
REDACTIONS = os.path.join(REPO, "public_redactions", "redactions.json")
MASKED_DIR = os.path.join(REPO, "public_redactions", "img")
NOTE = "*(screenshot not published: it shows private information)*"
COPIED = ["Licence.txt", ".nojekyll", ".gitignore"]
WRITTEN = COPIED + ["README.md", "site.json", "index.html"]
PRIVATE_BLOCK = re.compile(r"^[ \t]*<!-- private -->[ \t]*\r?\n.*?^[ \t]*<!-- /private -->[ \t]*(?:\r?\n|\Z)",
                           re.S | re.M)
PROTOCOL_LINK = re.compile(r"\[([^\]]*)\]\((\d{3}-[A-Za-z0-9_-]+)\.md(?:#[^)\s]*)?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\((img/[^)\s]+)(?:\s+\"[^\"]*\")?\)(?:\{[^}]*\})?")
TOKEN = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{16,}|"
                   r"AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|\bsk-(?:proj-)?[A-Za-z0-9]{20,}|"
                   r"-----BEGIN [A-Z ]*PRIVATE KEY")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
TEXT_EXT = (".md", ".html", ".htm", ".json", ".txt", ".js", ".css", ".py", ".lua", ".ps1", ".yml")


def git_roots(path):
    r = subprocess.run(["git", "-C", path, "rev-list", "--max-parents=0", "--all"], capture_output=True, text=True)
    return set(r.stdout.split()) if r.returncode == 0 else set()


def remote_to(public_url):
    """Name of a remote of this repository that points to the public repository, if any."""
    m = re.match(r"https?://([^/]+)/(.+?)/*$", public_url or "")
    if not m:
        return None
    host, path = m.group(1).lower(), m.group(2).lower()
    r = subprocess.run(["git", "-C", REPO, "remote", "-v"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            url = parts[1].lower().replace(":", "/")
            if host in url and re.search(r"/%s(\.git)?/*$" % re.escape(path), url):
                return parts[0]
    return None


def load_redactions():
    if not os.path.isfile(REDACTIONS):
        return [], []
    with open(REDACTIONS, encoding="utf-8") as f:
        data = json.load(f)
    text = sorted(data.get("text", []), key=lambda r: -len(r["find"]))  # longest first
    return text, data.get("images", [])


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def clean(dest):
    for d in ("md", "docs", "tools"):
        shutil.rmtree(os.path.join(dest, d), ignore_errors=True)
    for f in WRITTEN:
        if os.path.isfile(os.path.join(dest, f)):
            os.remove(os.path.join(dest, f))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dest", nargs="?", help="folder of the public repository")
    ap.add_argument("--pandoc", help="pandoc executable, passed to tools/build.py")
    a = ap.parse_args()
    if build.SETTINGS.get("public_only"):
        sys.exit("this is the public repository: run the export from the private one")
    public_settings = dict(build.SETTINGS.get("public_export") or {})
    dest = a.dest or public_settings.get("dest")
    public_settings.pop("dest", None)
    if not dest:
        sys.exit("give the folder of the public repository, or set public_export.dest in site.json")
    dest = os.path.realpath(os.path.join(REPO, os.path.expanduser(dest)))
    if dest == REPO or dest.startswith(REPO + os.sep):
        sys.exit("the public repository must be outside this one: " + dest)
    if os.path.exists(os.path.join(dest, ".git")) and git_roots(REPO) & git_roots(dest):
        sys.exit("STOP: %s shares git history with this private repository: pushing it would publish "
                 "every protocol. Make the public repository with a fresh history (git init)." % dest)

    pages = build.load_pages()
    public = sorted(p["slug"] for p in pages if p["public"])
    private = sorted((p["slug"] for p in pages if not p["public"]), key=len, reverse=True)
    if not public:
        sys.exit("no protocol has public: true in its YAML header")
    text_red, image_red = load_redactions()
    removed = {r["image"] for r in image_red if r.get("action") == "remove"}
    masked = {r["image"] for r in image_red if r.get("action") == "mask"}
    errors, unlinked, images, hits = [], [], set(), 0

    clean(dest)
    # the protocols: redactions, links to private protocols, images
    for slug in public:
        text = original = read(os.path.join(REPO, "md", slug + ".md"))
        for r in text_red:
            n = text.count(r["find"])
            if n:
                text = text.replace(r["find"], r["replace"])
                hits += n
            elif r.get("protocol") == slug and r["find"] not in original:
                errors.append("%s: a redaction no longer matches the text (update public_redactions/redactions.json): %r"
                              % (slug, r["find"][:60]))

        def image(m):
            ref = unquote(m.group(1))
            if ref in removed:
                return NOTE
            images.add(ref)
            return m.group(0)

        def link(m, slug=slug):
            if m.group(2) in public:
                return m.group(0)
            unlinked.append("%s -> %s" % (slug, m.group(2)))
            return m.group(1)

        text = PROTOCOL_LINK.sub(link, IMAGE.sub(image, text))
        write(os.path.join(dest, "md", slug + ".md"), text)
    for ref in sorted(images):
        src = os.path.join(MASKED_DIR, ref[len("img/"):]) if ref in masked else os.path.join(REPO, "md", ref)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(dest, "md", ref)), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, "md", ref))
        else:
            errors.append("missing image: " + src)

    # the rest of the repository
    shutil.copytree(os.path.join(REPO, "docs", "assets"), os.path.join(dest, "docs", "assets"))
    shutil.copytree(os.path.join(REPO, "tools"), os.path.join(dest, "tools"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in COPIED:
        if os.path.isfile(os.path.join(REPO, f)):
            shutil.copy2(os.path.join(REPO, f), os.path.join(dest, f))
    readme = PRIVATE_BLOCK.sub("", read(os.path.join(REPO, "README.md")))
    if "<!-- private -->" in readme or "<!-- /private -->" in readme:
        errors.append("README.md: unbalanced <!-- private --> / <!-- /private --> lines")
    write(os.path.join(dest, "README.md"), readme)
    redirects = []
    for fn in sorted(os.listdir(os.path.join(REPO, "docs"))):
        if fn.endswith((".html", ".htm")):
            page = read(os.path.join(REPO, "docs", fn))
            m = re.search(r'url=([^"#]+)\.html', page)
            if 'content="legacy redirect"' in page and m and m.group(1) in public:
                shutil.copy2(os.path.join(REPO, "docs", fn), os.path.join(dest, "docs", fn))
                redirects.append(fn)
    public_settings["public_only"] = True
    write(os.path.join(dest, "site.json"), json.dumps(public_settings, indent=2) + "\n")

    # the website of the public repository
    if not errors:
        cmd = [sys.executable, os.path.join(dest, "tools", "build.py")] + (["--pandoc", a.pandoc] if a.pandoc else [])
        if subprocess.run(cmd, cwd=dest).returncode != 0:
            errors.append("the website build failed in " + dest)

    # checks of the result
    emails = set()
    for root, dirs, files in os.walk(dest):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fn in files:
            if not fn.endswith(TEXT_EXT):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, dest)
            text = read(path)
            content = (rel.startswith(("md" + os.sep, "docs" + os.sep)) and not rel.startswith(
                os.path.join("docs", "assets") + os.sep)) or rel == "index.html"
            errors.extend("%s names the private protocol %s" % (rel, s) for s in private if s in text)
            errors.extend("%s: token-like string %s..." % (rel, m.group(0)[:10]) for m in TOKEN.finditer(text))
            if content:
                errors.extend("%s still contains a redacted value: %r" % (rel, r["find"][:40])
                              for r in text_red if r["find"] in text)
                emails.update(EMAIL.findall(text))
                if fn.endswith(".html"):
                    for src in re.findall(r'<img[^>]+src="\.\./(md/img/[^"]+)"', text):
                        if not os.path.isfile(os.path.join(dest, unquote(src))):
                            errors.append("%s shows a missing image %s" % (rel, src))

    if errors:
        clean(dest)
        print("\n".join("ERROR " + e for e in errors), file=sys.stderr)
        sys.exit("export stopped (%d error(s)); nothing is left in %s/md and %s/docs" % (len(errors), dest, dest))
    print("exported to %s: %d public protocol(s): %s" % (dest, len(public), ", ".join(public)))
    print("  %d image(s), %d redaction(s) applied, %d former address(es) kept, %d link(s) to private "
          "protocols turned into text" % (len(images), hits, len(redirects), len(unlinked)))
    if emails:
        print("  e-mail addresses on the public pages (check them): " + ", ".join(sorted(emails)))
    remote = remote_to(public_settings.get("repo_url"))
    if remote:
        print("\nWARNING: the remote '%s' of this PRIVATE repository points to the public repository %s: "
              "change it (git remote set-url %s <url of the private repository>) before pushing anything."
              % (remote, public_settings.get("repo_url"), remote))
    print("\nnext: check the website (cd %s && python3 -m http.server 8000), then commit and push it." % dest)


if __name__ == "__main__":
    main()
