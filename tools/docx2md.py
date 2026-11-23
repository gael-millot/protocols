#!/usr/bin/env python3
"""Import Word protocols (.docx) as Markdown files of this repository.

usage: python3 tools/docx2md.py [--pandoc PANDOC] [--out md] FILE.docx [FILE.docx ...]

For each "Protocol NNN-revR NAME.docx", writes:
  md/NNN-name.md               Markdown with a YAML header (title, protocol, revision, date, source)
  md/img/NNN-name/imageK.png   its images (Word metafiles rendered to PNG, big screenshots shrunk)

An existing md/NNN-name.md is overwritten: import only what is not yet edited in Markdown.

Needs pandoc >= 3.1 (on the PATH, or --pandoc, or $PANDOC; on WSL the pandoc.exe bundled
with RStudio is found too). EMF/WMF images are rendered with Windows PowerShell (from WSL or
Windows), else Inkscape or LibreOffice, else kept as they are (not displayed by browsers).
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docx_prep  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILTER = os.path.join(REPO, "tools", "filters", "docx_import.lua")
PS1 = os.path.join(REPO, "tools", "img_convert.ps1")
MAX_WIDTH = 1600  # pixels: wider images are shrunk (displayed at most ~850 px wide)
# raw_tex must stay enabled in the writer: without it, pandoc 3.10 drops the character after a
# backslash (\% -> \\); the reader of tools/build.py disables raw_tex, so nothing is read as TeX.
WRITER = "markdown-smart-citations-simple_tables-multiline_tables"
BUILD_READER = "markdown-smart-citations-raw_tex"  # same as tools/build.py
META_ORDER = ["title", "protocol", "revision", "date", "source"]
NAME_RE = re.compile(r"^Protocol\s+(\d+)-rev(\d+)\s+(.*)$", re.I)
PANDOC_CANDIDATES = [
    "/mnt/c/Program Files/RStudio/resources/app/bin/quarto/bin/tools/pandoc.exe",
    r"C:\Program Files\RStudio\resources\app\bin\quarto\bin\tools\pandoc.exe",
]


def parse_name(path):
    base = os.path.splitext(os.path.basename(path))[0]
    m = NAME_RE.match(base)
    if m:
        return m.group(1), m.group(2), re.sub(r"\s+", " ", m.group(3)).strip()
    return "", "0", base.strip()


def slugify(num, name):
    name = re.sub(r"\b(19|20)\d{6}\b", "", name)  # dates such as 20171025
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return "%s-%s" % (num, s) if num else s


def find_pandoc(arg):
    for c in [arg, os.environ.get("PANDOC"), shutil.which("pandoc")] + PANDOC_CANDIDATES:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    sys.exit("pandoc not found: install it (sudo apt install pandoc) or use --pandoc")


def docx_info(path):
    """Last modification date and text width (inches) of the document."""
    z = zipfile.ZipFile(path)
    date = None
    if "docProps/core.xml" in z.namelist():
        core = z.read("docProps/core.xml").decode("utf8", "replace")
        m = re.search(r"<dcterms:modified[^>]*>(\d{4}-\d{2}-\d{2})", core)
        if m:
            date = m.group(1)
    mtime = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    if not date or mtime > date:
        date = mtime
    doc = z.read("word/document.xml").decode("utf8", "replace")
    tw = 6.3
    sz = re.findall(r'<w:pgSz [^>]*?w:w="(\d+)"', doc)
    mar = re.findall(r"<w:pgMar [^>]*>", doc)
    if sz and mar:
        left = re.search(r'w:left="(-?\d+)"', mar[-1])
        right = re.search(r'w:right="(-?\d+)"', mar[-1])
        if left and right:
            tw = (int(sz[-1]) - int(left.group(1)) - int(right.group(1))) / 1440.0
    return date, round(max(tw, 3.0), 2)


def sniff(path):
    with open(path, "rb") as f:
        h = f.read(64)
    if h.startswith(b"\x89PNG"):
        return ".png"
    if h[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if h[:4] == b"GIF8":
        return ".gif"
    if h[:4] == b"\x01\x00\x00\x00" and h[40:44] == b" EMF":
        return ".emf"
    if h[:4] == b"\xd7\xcd\xc6\x9a" or h[:4] in (b"\x01\x00\x09\x00", b"\x02\x00\x09\x00"):
        return ".wmf"
    if h.lstrip().startswith(b"<?xml") or b"<svg" in h:
        return ".svg"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return ".webp"
    if h[:2] in (b"II", b"MM"):
        return ".tif"
    if h[:2] == b"BM":
        return ".bmp"
    return None


def image_width(path):
    """Pixel width of a PNG, JPEG or GIF (None if unknown), standard library only."""
    with open(path, "rb") as f:
        data = f.read()
    if data.startswith(b"\x89PNG"):
        return int.from_bytes(data[16:20], "big")
    if data[:4] == b"GIF8":
        return int.from_bytes(data[6:8], "little")
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[i + 7:i + 9], "big")
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def to_windows(path):
    if os.name == "nt":
        return os.path.abspath(path)
    return subprocess.run(["wslpath", "-w", os.path.abspath(path)], capture_output=True,
                          text=True, check=True).stdout.strip()


def powershell():
    for c in ("powershell.exe", "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe"):
        if shutil.which(c) or os.path.isfile(c):
            return c
    return None


def run_image_jobs(jobs, tmproot):
    """jobs: list of (mode, input, output). Returns {output: True/False}."""
    if not jobs:
        return {}
    done = {}
    ps = powershell()
    if ps:
        listfile = os.path.join(tmproot, "img_jobs.tsv")
        winmap = {}
        with open(listfile, "w", encoding="utf-8-sig") as f:
            for mode, src, out in jobs:
                wout = to_windows(out)
                winmap[wout.lower()] = out
                f.write("%s\t%s\t%s\t%d\n" % (mode, to_windows(src), wout, MAX_WIDTH))
        r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", to_windows(PS1),
                            "-List", to_windows(listfile)], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                out = winmap.get(parts[1].lower())
                if out:
                    done[out] = parts[0] in ("OK", "SKIP") and (parts[0] == "SKIP" or os.path.isfile(out))
                    if parts[0] == "ERR":
                        print("  image conversion error: %s %s" % (out, " ".join(parts[2:])), file=sys.stderr)
        return done
    inkscape = shutil.which("inkscape")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    magick = shutil.which("magick") or shutil.which("convert")
    for mode, src, out in jobs:
        ok = False
        if mode == "emf" and inkscape:
            ok = subprocess.run([inkscape, src, "--export-type=png", "--export-filename=" + out,
                                 "--export-width=%d" % MAX_WIDTH, "--export-background=white"],
                                capture_output=True).returncode == 0
        elif mode == "emf" and soffice:
            d = os.path.dirname(out)
            ok = subprocess.run([soffice, "--headless", "--convert-to", "png", "--outdir", d, src],
                                capture_output=True).returncode == 0
            produced = os.path.join(d, os.path.splitext(os.path.basename(src))[0] + ".png")
            if ok and produced != out and os.path.isfile(produced):
                shutil.move(produced, out)
        elif mode == "resize" and magick:
            ok = subprocess.run([magick, src, "-resize", "%dx>" % MAX_WIDTH, out],
                                capture_output=True).returncode == 0
        done[out] = ok and os.path.isfile(out)
    return done


def convert(docx, pandoc, outdir, tmproot, keep_tmp=False):
    num, rev, name = parse_name(docx)
    slug = slugify(num, name)
    tmp = tempfile.mkdtemp(prefix=slug + "_", dir=tmproot)
    stats = docx_prep.prep(docx, os.path.join(tmp, "in.docx"))
    shutil.copy(FILTER, os.path.join(tmp, "docx_import.lua"))
    date, textwidth = docx_info(docx)
    cmd = [pandoc, "in.docx", "-f", "docx+styles", "-t", WRITER, "--wrap=none", "--columns=100",
           "-s", "--extract-media=img/" + slug, "--lua-filter=docx_import.lua",
           "-M", "import_number=" + num, "-M", "import_revision=" + rev,
           "-M", "import_name=" + name, "-M", "import_source=" + os.path.basename(docx),
           "-M", "import_date=" + date, "-M", "import_textwidth=%s" % textwidth,
           "-o", "out.md"]
    r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("pandoc failed on %s:\n%s" % (docx, r.stderr))
    warnings = [w for w in r.stderr.splitlines() if w.strip()]
    # same document as a pandoc AST, to check that the written Markdown reads back identically
    cmd_ast = [c if c != "out.md" else "out.json" for c in cmd if not c.startswith("--extract-media") and c != "-s"]
    cmd_ast[cmd_ast.index("-t") + 1] = "json"
    subprocess.run(cmd_ast, cwd=tmp, capture_output=True, text=True)
    plain = ""
    if os.path.isfile(os.path.join(tmp, "out.json")):
        with open(os.path.join(tmp, "out.json"), encoding="utf-8") as f:
            plain = ast_text(json.load(f))
    with open(os.path.join(tmp, "out.md"), encoding="utf-8") as f:
        md = f.read()

    # media: img/<slug>/media/x -> md/img/<slug>/x, real extension from the file content
    media = os.path.join(tmp, "img", slug, "media")
    dest = os.path.join(outdir, "img", slug)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    jobs, renames = [], {}
    if os.path.isdir(media):
        os.makedirs(dest, exist_ok=True)
        for fn in sorted(os.listdir(media)):
            src = os.path.join(media, fn)
            stem, ext = os.path.splitext(fn)
            real = sniff(src) or ext.lower()
            if real == ".jpg" and ext.lower() == ".jpeg":
                real = ".jpeg"
            target = os.path.join(dest, stem + real)
            shutil.copy(src, target)
            renames[fn] = stem + real
            if real in (".emf", ".wmf"):
                jobs.append(("emf", target, os.path.join(dest, stem + ".png")))
            elif real in (".png", ".jpg", ".jpeg", ".gif"):
                w = image_width(target)
                if w and w > MAX_WIDTH:
                    jobs.append(("resize", target, target + ".resized" + real))
    for fn, new in renames.items():
        md = md.replace("img/%s/media/%s" % (slug, fn), "img/%s/%s" % (slug, new))
    if not keep_tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"docx": docx, "num": num, "slug": slug, "md": md, "plain": plain, "jobs": jobs, "stats": stats, "warnings": warnings,
            "dest": dest}


def tidy_markdown(md, source=None):
    """Front matter keys in a fixed order; `text` typed with backticks in Word -> inline code."""
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    if m:
        lines = m.group(1).split("\n")
        if source:  # exact file name (pandoc collapses the double spaces of -M values)
            lines = [l for l in lines if not l.startswith("source:")] + ["source: " + json.dumps(source)]
        rank = {k: i for i, k in enumerate(META_ORDER)}
        lines.sort(key=lambda l: rank.get(l.split(":", 1)[0].strip(), 99))
        md = "---\n" + "\n".join(lines) + "\n---\n" + md[m.end():]
    # code blocks carry the class "importcode" (set by docx_import.lua) only to be written fenced
    md = re.sub(r"(?m)^([ \t]*)(`{3,}|~{3,})[ \t]*(?:\{\.importcode\}|importcode)[ \t]*$", r"\1\2", md)
    md = md.replace("`{.importcode}", "`")
    out, fence = [], None
    yaml_lines = md[:md.find("\n---\n", 4) + 4].count("\n") + 1 if md.startswith("---\n") else 0
    for k, line in enumerate(md.split("\n")):
        f = re.match(r"^\s*(`{3,}|~{3,})", line)
        if f:
            if fence is None:
                fence = f.group(1)
            elif line.strip().startswith(fence):
                fence = None
            out.append(line)
            continue
        if fence is None and k >= yaml_lines:
            # a literal "---" line of the Word text is not a horizontal rule (pandoc writes those longer)
            line = re.sub(r"^([ \t>]*)(-{3}|\*{3}|_{3})([ \t]*)$", r"\1\\\2\3", line)
            # "# Title with if(){}": an ending {} would be read as heading attributes
            if re.match(r"^#{1,6} ", line):
                line = re.sub(r"\{(\s*)\}(\s*)$", r"\\{\1\\}\2", line)
        if fence is None and "\\`" in line:
            def repl(mo):
                body = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", mo.group(1))
                after = mo.string[mo.end():mo.end() + 1]
                # `code` typed in Word: no space at its ends, nothing glued after it, short;
                # LaTeX-style quotes (`*' ... `.') are not code
                if (not body.strip() or body != body.strip() or len(body) > 80
                        or (after and after.isalnum()) or "'" in body[:2]):
                    return mo.group(0)
                return "`%s`" % body
            line = re.sub(r"\\`([^`\n]+?)\\`", repl, line)
        out.append(line)
    return "\n".join(out)


LINK_PLACEHOLDER = re.compile(r"\[([^\]]*)\]\(protocol:(\d+)\)")
MENTION = re.compile(r"(?<![\w\[/#.-])([Pp]rotocols?|PROTOCOLS?)(\s+)(\d{1,3})(?![\w-]|\.\d)")
MASK = re.compile(r"(`+[^`]*?`+|!?\[[^\]]*\]\([^)]*\)|<https?://[^>]*>|https?://\S+)")


def protocol_map(outdir, results):
    """protocol number (no leading zero) -> slug; None when several protocols share a number."""
    pmap = {}

    def add(num, slug):
        if not num:
            return
        num = num.lstrip("0") or "0"
        if num in pmap and pmap[num] != slug:
            pmap[num] = None
        else:
            pmap.setdefault(num, slug)

    for fn in sorted(os.listdir(outdir)) if os.path.isdir(outdir) else []:
        if fn.endswith(".md"):
            with open(os.path.join(outdir, fn), encoding="utf-8") as f:
                head = f.read(2000)
            m = re.search(r"(?m)^protocol:\s*['\"]?(\d+)", head)
            if m:
                add(m.group(1), fn[:-3])
    for res in results:
        add(res["num"], res["slug"])
    return pmap


def link_protocols(md, pmap, self_slug):
    """Links "protocol NN" (Word file links and plain mentions) to md/NN-name.md."""
    def target(num):
        slug = pmap.get(num.lstrip("0") or "0")
        return slug if slug and slug != self_slug else None

    md = LINK_PLACEHOLDER.sub(
        lambda m: "[%s](%s.md)" % (m.group(1), target(m.group(2))) if target(m.group(2)) else m.group(1), md)

    def link_mentions(text):
        def repl(m):
            t = target(m.group(3))
            return "[%s%s%s](%s.md)" % (m.group(1), m.group(2), m.group(3), t) if t else m.group(0)
        parts = MASK.split(text)
        return "".join(MENTION.sub(repl, part) if i % 2 == 0 else part for i, part in enumerate(parts))

    out, fence, in_yaml = [], None, False
    for k, line in enumerate(md.split("\n")):
        if k == 0 and line == "---":
            in_yaml = True
            out.append(line)
            continue
        if in_yaml:
            if line == "---":
                in_yaml = False
            out.append(line)
            continue
        f = re.match(r"^\s*(`{3,}|~{3,})", line)
        if f:
            if fence is None:
                fence = f.group(1)
            elif line.strip().startswith(fence):
                fence = None
            out.append(line)
            continue
        out.append(line if fence else link_mentions(line))
    return "\n".join(out)


def finish_images(results, tmproot):
    jobs = [j for res in results for j in res["jobs"]]
    done = run_image_jobs(jobs, tmproot)
    for res in results:
        md = res["md"]
        for mode, src, out in res["jobs"]:
            ok = done.get(out)
            if mode == "emf":
                if ok:
                    outdir = os.path.dirname(os.path.dirname(res["dest"]))
                    md = md.replace(os.path.relpath(src, outdir).replace(os.sep, "/"),
                                    os.path.relpath(out, outdir).replace(os.sep, "/"))
                    os.remove(src)
                else:
                    res["warnings"].append("metafile not converted: " + os.path.basename(src))
            elif mode == "resize":
                if ok and os.path.isfile(out) and os.path.getsize(out) < os.path.getsize(src):
                    os.replace(out, src)
                elif os.path.isfile(out):
                    os.remove(out)
        res["md"] = md


def ast_text(node):
    """Text of a pandoc JSON AST (blocks only), without attributes, link targets or table widths."""
    out = []

    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict) and "t" in n:
            t, c = n["t"], n.get("c")
            if t in ("Str", "Math"):
                out.append(c if t == "Str" else c[1])
            elif t == "Code":
                out.append(c[1])
            elif t == "CodeBlock":
                out.append(" %s " % c[1])
            elif t in ("RawInline", "RawBlock", "HorizontalRule"):
                out.append(" ")
            elif t in ("Space", "SoftBreak", "LineBreak"):
                out.append(" ")
            elif t == "Link":
                walk(c[1])
            elif t == "Image":
                out.append(" ")
            elif t in ("Header",):
                out.append(" ")
                walk(c[2])
                out.append(" ")
            elif t in ("Div", "Span"):
                walk(c[1:])
            elif t == "Figure":
                walk(c[2])
            elif t == "Table":
                walk(c[1])
                for part in (c[3], c[4], c[5]):
                    walk(part)
            elif t == "Cell":
                walk(c[4])
            elif c is not None:
                out.append(" ")
                walk(c)
                out.append(" ")
        elif isinstance(n, dict):
            for k in ("blocks", "c"):
                if k in n:
                    walk(n[k])

    if isinstance(node, dict) and "blocks" in node:
        walk(node["blocks"])
    else:
        walk(node)
    return "".join(out)


def _norm_text(t):
    return re.sub(r"\s+", " ", t.replace("`", "")).strip()


def roundtrip_check(pandoc, md_path, plain):
    """The written Markdown, read as the website reads it, must give the imported text back."""
    r = subprocess.run([pandoc, os.path.basename(md_path), "-f", BUILD_READER, "-t", "json"],
                       cwd=os.path.dirname(md_path), capture_output=True, text=True)
    try:
        back = ast_text(json.loads(r.stdout))
    except ValueError:
        return "Markdown could not be read back by pandoc"
    a, b = _norm_text(plain), _norm_text(back)
    if not plain or a == b:
        return None
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    return "Markdown does not read back as imported, near: %r / %r" % (a[max(0, i - 50):i + 50], b[max(0, i - 50):i + 50])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docx", nargs="+")
    ap.add_argument("--pandoc")
    ap.add_argument("--out", default=os.path.join(REPO, "md"))
    ap.add_argument("--tmp", default=None, help="temporary directory (default: system temp)")
    ap.add_argument("--keep-tmp", action="store_true")
    a = ap.parse_args()
    pandoc = find_pandoc(a.pandoc)
    os.makedirs(a.out, exist_ok=True)
    tmproot = tempfile.mkdtemp(prefix="docx2md_", dir=a.tmp)
    results = []
    for d in a.docx:
        try:
            res = convert(d, pandoc, a.out, tmproot, a.keep_tmp)
        except Exception as e:  # keep going with the other files
            print("FAILED %s: %s" % (d, e), file=sys.stderr)
            continue
        results.append(res)
    finish_images(results, tmproot)
    pmap = protocol_map(a.out, results)
    for res in results:
        res["md"] = link_protocols(tidy_markdown(res["md"], os.path.basename(res["docx"])), pmap, res["slug"])
        path = os.path.join(a.out, res["slug"] + ".md")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(res["md"])
        problem = roundtrip_check(pandoc, path, res["plain"])
        if problem:
            res["warnings"].append(problem)
        s = res["stats"]
        print("%s -> %s  [code blocks %d, inline code %d, red %d, mark %d, tabs %d]%s" % (
            os.path.basename(res["docx"]), os.path.relpath(path, REPO), s["code_blocks"], s["inline_code"],
            s["red"], s["mark"], s["tabs"], "".join("\n    ! " + w for w in res["warnings"])))
    if not a.keep_tmp:
        shutil.rmtree(tmproot, ignore_errors=True)


if __name__ == "__main__":
    main()
