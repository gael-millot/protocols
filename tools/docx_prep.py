#!/usr/bin/env python3
"""Pre-process a Word .docx so that pandoc keeps the formatting conventions of the protocols.

Pandoc ignores direct formatting (fonts, colours, shading). In the protocols, code is written
with a monospace font (Lucida Console, Courier New, Consolas...) on a grey shaded paragraph.
This script rewrites the docx XML (in a copy) before pandoc reads it:

- paragraphs whose text is entirely monospace become one "Source Code" paragraph per block
  (consecutive code paragraphs are merged with line breaks), read by pandoc as a code block;
- monospace runs inside a normal paragraph get the "Verbatim Char" style (pandoc inline code);
- red text and highlighted text outside code get the "Proto Red" / "Proto Mark" styles,
  turned into [text]{.red} / [text]{.mark} by tools/filters/docx_import.lua (pandoc +styles);
- tabs between words of a normal paragraph become a separate " \u21e5 " run: tab-aligned lines
  (acronym lists, field descriptions) are turned into tables by docx_import.lua;
- hidden text (w:vanish) is removed.

Standard library only. Usage: docx_prep.py IN.docx OUT.docx
"""

import copy
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

MONO_RE = re.compile(
    r"courier|consolas|lucida console|lucida sans typewriter|monaco|menlo|mono|"
    r"source code|inconsolata|typewriter|fixedsys|terminal",
    re.I,
)
# red-like colours used to flag important text outside code
RED_COLORS = {"FF0000", "C00000", "E00000", "FF3300", "CC0000"}

CODE_PSTYLE_ID = "ProtoSourceCode"
CODE_PSTYLE_NAME = "Source Code"
CODE_RSTYLE_ID = "ProtoVerbatimChar"
CODE_RSTYLE_NAME = "Verbatim Char"
RED_RSTYLE_ID = "ProtoRed"
RED_RSTYLE_NAME = "Proto Red"
MARK_RSTYLE_ID = "ProtoMark"
MARK_RSTYLE_NAME = "Proto Mark"
TAB_MARK = "\u21e5"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# subtrees whose runs/paragraphs do not belong to the current paragraph text flow
SKIP_TAGS = {
    W + "drawing", W + "pict", W + "object", W + "del", W + "moveFrom",
    "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent",
    W + "txbxContent", W + "fldData",
}


def register_namespaces(xml_bytes):
    """Keep the original namespace prefixes when re-serialising.

    Word declares some prefixes (a:, pic:, a14:...) on inner elements; ElementTree moves every
    declaration to the root element and would rename unknown prefixes ns0, ns1...; pandoc looks
    elements up by prefix, so all prefixes declared anywhere in the file must be registered."""
    seen = {}
    for prefix, uri in re.findall(rb'xmlns:([A-Za-z0-9_]+)="([^"]+)"', xml_bytes):
        p = prefix.decode()
        if p not in seen:
            seen[p] = uri.decode()
    for p, uri in seen.items():
        try:
            ET.register_namespace(p, uri)
        except ValueError:
            pass


class Styles:
    def __init__(self, styles_xml, theme_xml):
        self.styles = {}
        self.default_font = None
        self.default_pstyle = None
        self.major = self.minor = None
        if theme_xml:
            th = theme_xml.decode("utf8", "replace")
            m = re.search(r"<a:majorFont>\s*<a:latin typeface=\"([^\"]*)\"", th)
            n = re.search(r"<a:minorFont>\s*<a:latin typeface=\"([^\"]*)\"", th)
            self.major = m.group(1) if m else None
            self.minor = n.group(1) if n else None
        if not styles_xml:
            return
        root = ET.fromstring(styles_xml)
        dd = root.find(W + "docDefaults")
        if dd is not None:
            rf = dd.find(W + "rPrDefault/" + W + "rPr/" + W + "rFonts")
            self.default_font = self._font_of(rf)
        for s in root.findall(W + "style"):
            sid = s.get(W + "styleId")
            name = s.find(W + "name")
            based = s.find(W + "basedOn")
            rpr = s.find(W + "rPr")
            ppr = s.find(W + "pPr")
            info = {
                "type": s.get(W + "type"),
                "name": name.get(W + "val") if name is not None else sid,
                "basedOn": based.get(W + "val") if based is not None else None,
                "font": self._font_of(rpr.find(W + "rFonts")) if rpr is not None else None,
                "shd": None,
            }
            if ppr is not None and ppr.find(W + "shd") is not None:
                info["shd"] = ppr.find(W + "shd").get(W + "fill")
            self.styles[sid] = info
            if s.get(W + "type") == "paragraph" and s.get(W + "default") == "1":
                self.default_pstyle = sid
        self.root = root

    def _font_of(self, rf):
        if rf is None:
            return None
        theme = rf.get(W + "asciiTheme") or rf.get(W + "hAnsiTheme")
        if theme:
            return self.major if theme.startswith("major") else self.minor
        return rf.get(W + "ascii") or rf.get(W + "hAnsi")

    def style_font(self, sid, depth=0):
        while sid and depth < 20:
            info = self.styles.get(sid)
            if info is None:
                return None
            if info["font"]:
                return info["font"]
            sid = info["basedOn"]
            depth += 1
        return None

    def name(self, sid):
        info = self.styles.get(sid)
        return info["name"] if info else sid


def is_mono(font):
    return bool(font) and bool(MONO_RE.search(font)) and "gothic" not in font.lower()


def iter_runs(elem):
    """Runs of a paragraph, without descending into drawings, text boxes, deleted text."""
    for child in elem:
        if child.tag in SKIP_TAGS:
            continue
        if child.tag == W + "r":
            yield child
        else:
            yield from iter_runs(child)


def run_text(r):
    out = []
    for child in r:
        if child.tag == W + "t":
            out.append(child.text or "")
        elif child.tag == W + "tab":
            out.append("\t")
    return "".join(out)


def pstyle_id(p):
    ppr = p.find(W + "pPr")
    if ppr is not None:
        ps = ppr.find(W + "pStyle")
        if ps is not None:
            return ps.get(W + "val")
    return None


def effective_font(r, para_sid, styles):
    rpr = r.find(W + "rPr")
    if rpr is not None:
        f = styles._font_of(rpr.find(W + "rFonts"))
        if f:
            return f
        rs = rpr.find(W + "rStyle")
        if rs is not None:
            f = styles.style_font(rs.get(W + "val"))
            if f:
                return f
    f = styles.style_font(para_sid or styles.default_pstyle)
    if f:
        return f
    return styles.default_font


def para_mark_font(p, styles):
    ppr = p.find(W + "pPr")
    if ppr is not None:
        rpr = ppr.find(W + "rPr")
        if rpr is not None:
            f = styles._font_of(rpr.find(W + "rFonts"))
            if f:
                return f
    return styles.style_font(pstyle_id(p) or styles.default_pstyle) or styles.default_font


def para_shading(p, styles):
    ppr = p.find(W + "pPr")
    if ppr is not None and ppr.find(W + "shd") is not None:
        fill = ppr.find(W + "shd").get(W + "fill")
        if fill and fill.lower() not in ("auto", "ffffff"):
            return fill.upper()
    sid = pstyle_id(p)
    depth = 0
    while sid and depth < 20:
        info = styles.styles.get(sid)
        if not info:
            break
        if info["shd"] and info["shd"].lower() not in ("auto", "ffffff"):
            return info["shd"].upper()
        sid = info["basedOn"]
        depth += 1
    return None


def has_visual(p):
    """Paragraph containing an image, an equation or a text box (never turned into code)."""
    for el in p.iter():
        t = el.tag
        if t in (W + "drawing", W + "pict", W + "object") or t.endswith("}oMath") or t.endswith("}oMathPara"):
            return True
    return False


def classify(p, styles):
    """Return ('code'|'text'|'empty', runs_info) for a paragraph."""
    sid = pstyle_id(p)
    runs = list(iter_runs(p))
    info = []
    has_text = False
    all_mono = True
    for r in runs:
        rpr = r.find(W + "rPr")
        if rpr is not None and rpr.find(W + "vanish") is not None:
            info.append((r, None, True))
            continue
        txt = run_text(r)
        f = effective_font(r, sid, styles)
        mono = is_mono(f)
        info.append((r, mono, False))
        if txt.strip():
            has_text = True
            if not mono:
                all_mono = False
    if has_visual(p):
        return "text", info
    if not has_text:
        return "empty", info
    return ("code" if all_mono else "text"), info


def set_pstyle(p, sid):
    ppr = p.find(W + "pPr")
    if ppr is None:
        ppr = ET.Element(W + "pPr")
        p.insert(0, ppr)
    ps = ppr.find(W + "pStyle")
    if ps is None:
        ps = ET.Element(W + "pStyle")
        ppr.insert(0, ps)
    ps.set(W + "val", sid)
    # no list numbering / indentation for code blocks (pandoc would make lists or quotes)
    for tag in ("numPr", "ind"):
        el = ppr.find(W + tag)
        if el is not None:
            ppr.remove(el)


def set_rstyle(r, sid):
    rpr = r.find(W + "rPr")
    if rpr is None:
        rpr = ET.Element(W + "rPr")
        r.insert(0, rpr)
    rs = rpr.find(W + "rStyle")
    if rs is None:
        rs = ET.Element(W + "rStyle")
        rpr.insert(0, rs)
    rs.set(W + "val", sid)


def run_color(r):
    rpr = r.find(W + "rPr")
    if rpr is None:
        return None
    c = rpr.find(W + "color")
    return c.get(W + "val").upper() if c is not None and c.get(W + "val") else None


def run_highlight(r):
    rpr = r.find(W + "rPr")
    if rpr is None:
        return None
    h = rpr.find(W + "highlight")
    if h is not None and h.get(W + "val") not in (None, "none"):
        return h.get(W + "val")
    s = rpr.find(W + "shd")
    if s is not None and s.get(W + "fill") and s.get(W + "fill").upper() not in ("AUTO", "FFFFFF"):
        return "shd"
    return None


def remove_hidden(info):
    for r, _mono, hidden in info:
        if hidden:
            parent = PARENT.get(r)
            if parent is not None:
                parent.remove(r)


PARENT = {}


def merge_code_group(group, styles, stats):
    """Merge consecutive code paragraphs into the first one, joined by line breaks."""
    first = group[0][0]
    set_pstyle(first, CODE_PSTYLE_ID)
    parent = PARENT[first]
    for p, kind in group[1:]:
        # line break between paragraphs
        br_run = ET.SubElement(first, W + "r")
        ET.SubElement(br_run, W + "br")
        for child in list(p):
            if child.tag == W + "pPr":
                continue
            first.append(child)
        parent.remove(p)
    stats["code_blocks"] += 1
    stats["code_lines"] += len(group)


def process_container(container, styles, stats):
    children = list(container)
    i = 0
    n = len(children)
    while i < n:
        el = children[i]
        if el.tag != W + "p":
            i += 1
            continue
        kind, info = classify(el, styles)
        remove_hidden(info)
        if kind != "code":
            if kind == "text":
                mark_inline(info, stats)
                mark_tabs(el, stats)
            i += 1
            continue
        # start of a code group: extend with code paragraphs and inner empty code-like paragraphs
        group = [(el, kind)]
        shade = para_shading(el, styles)
        j = i + 1
        pending_empty = []
        while j < n and children[j].tag == W + "p":
            k2, info2 = classify(children[j], styles)
            if k2 == "code":
                remove_hidden(info2)
                group.extend(pending_empty)
                pending_empty = []
                group.append((children[j], k2))
                j += 1
                continue
            if k2 == "empty":
                same_shade = shade is not None and para_shading(children[j], styles) == shade
                if same_shade or is_mono(para_mark_font(children[j], styles)):
                    pending_empty.append((children[j], k2))
                    j += 1
                    continue
            break
        merge_code_group(group, styles, stats)
        i = j - len(pending_empty)


def mark_tabs(p, stats):
    """Tabs between words -> separate unformatted run " \u21e5 " (leading/trailing tabs untouched)."""
    runs = list(iter_runs(p))
    seq = [(r, c) for r in runs for c in r if c.tag in (W + "t", W + "tab")]
    text_idx = [i for i, (r, c) in enumerate(seq) if c.tag == W + "t" and (c.text or "").strip()]
    if len(text_idx) < 2:
        return
    first, last = text_idx[0], text_idx[-1]
    internal = {id(c) for i, (r, c) in enumerate(seq) if c.tag == W + "tab" and first < i < last}
    if not internal:
        return
    for r in runs:
        if not any(id(c) in internal for c in r):
            continue
        parent = PARENT[r]
        idx = list(parent).index(r)
        rpr = r.find(W + "rPr")
        pieces, cur = [], []
        for child in list(r):
            if child.tag == W + "rPr":
                continue
            if id(child) in internal:
                pieces.extend([cur, None])
                cur = []
            else:
                cur.append(child)
        pieces.append(cur)
        new_runs = []
        for piece in pieces:
            if piece is None:
                mr = ET.Element(W + "r")
                t = ET.SubElement(mr, W + "t")
                t.text = " " + TAB_MARK + " "
                t.set(XML_SPACE, "preserve")
                new_runs.append(mr)
                stats["tabs"] += 1
            elif piece:
                nr = ET.Element(W + "r", dict(r.attrib))
                if rpr is not None:
                    nr.append(copy.deepcopy(rpr))
                nr.extend(piece)
                new_runs.append(nr)
        parent.remove(r)
        for k, nr in enumerate(new_runs):
            parent.insert(idx + k, nr)
            PARENT[nr] = parent


def mark_inline(info, stats):
    for r, mono, hidden in info:
        if hidden or mono is None:
            continue
        txt = run_text(r)
        if not txt.strip():
            continue
        if mono:
            set_rstyle(r, CODE_RSTYLE_ID)
            stats["inline_code"] += 1
        elif run_color(r) in RED_COLORS:
            set_rstyle(r, RED_RSTYLE_ID)
            stats["red"] += 1
        elif run_highlight(r):
            set_rstyle(r, MARK_RSTYLE_ID)
            stats["mark"] += 1


def add_styles(styles_root):
    existing = {s.get(W + "styleId") for s in styles_root.findall(W + "style")}

    def add(sid, name, stype):
        if sid in existing:
            return
        s = ET.SubElement(styles_root, W + "style")
        s.set(W + "type", stype)
        s.set(W + "customStyle", "1")
        s.set(W + "styleId", sid)
        n = ET.SubElement(s, W + "name")
        n.set(W + "val", name)

    add(CODE_PSTYLE_ID, CODE_PSTYLE_NAME, "paragraph")
    add(CODE_RSTYLE_ID, CODE_RSTYLE_NAME, "character")
    add(RED_RSTYLE_ID, RED_RSTYLE_NAME, "character")
    add(MARK_RSTYLE_ID, MARK_RSTYLE_NAME, "character")


def prep(in_path, out_path):
    zin = zipfile.ZipFile(in_path)
    names = zin.namelist()
    doc_xml = zin.read("word/document.xml")
    styles_xml = zin.read("word/styles.xml") if "word/styles.xml" in names else None
    theme_name = next((n for n in names if n.startswith("word/theme/") and n.endswith(".xml")), None)
    theme_xml = zin.read(theme_name) if theme_name else None
    register_namespaces(doc_xml)
    if styles_xml:
        register_namespaces(styles_xml)
    styles = Styles(styles_xml, theme_xml)
    root = ET.fromstring(doc_xml)
    PARENT.clear()
    for parent in root.iter():
        for child in parent:
            PARENT[child] = parent
    stats = {"code_blocks": 0, "code_lines": 0, "inline_code": 0, "red": 0, "mark": 0, "tabs": 0}
    containers = [el for el in root.iter() if any(c.tag == W + "p" for c in el)]
    for c in containers:
        process_container(c, styles, stats)
    new_doc = ET.tostring(root, encoding="UTF-8")
    if styles_xml:
        add_styles(styles.root)
        new_styles = ET.tostring(styles.root, xml_declaration=True, encoding="UTF-8")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename == "word/document.xml":
                zout.writestr(item, new_doc)
            elif item.filename == "word/styles.xml" and styles_xml:
                zout.writestr(item, new_styles)
            else:
                zout.writestr(item, zin.read(item.filename))
    return stats


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: docx_prep.py IN.docx OUT.docx")
    print(prep(sys.argv[1], sys.argv[2]))
