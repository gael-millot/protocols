[![License: GPL-3.0](https://img.shields.io/badge/licence-GPL%20(%3E%3D3)-green?style=plastic)](https://www.gnu.org/licenses)

<br /><br />
## TABLE OF CONTENTS

   - [CONTENT](#content)
   - [WEBSITE](#website)
   - [EDITING A PROTOCOL](#editing-a-protocol)
   - [BUILDING THE WEBSITE](#building-the-website)
   - [ADDING A PROTOCOL](#adding-a-protocol)
   - [REPOSITORY CONTENT](#repository-content)
   - [VERSIONS](#versions)
   - [LICENCE](#licence)
   - [CREDITS](#credits)
   - [WHAT'S NEW IN](#whats-new-in)



<br /><br />
## CONTENT

My bioinformatics notes, first written in MS Word, now kept as Markdown files (folder `md/`) and published as a website with the Read the Docs look.


<br /><br />
## WEBSITE

https://gael-millot.github.io/protocols/

- Left menu: protocols grouped by category, with the sections of the current protocol.
- Home page: catalogue with an instant filter (name, number or topic).
- Search box: full-text search of all the protocols (commands included).
- Code blocks have a copy button; sections are numbered automatically.



<br /><br />
## EDITING A PROTOCOL

1. Open `md/NNN-name.md` in any text editor (from Windows: `\\wsl$\Ubuntu-26.04\home\gmillot\Git_projects\protocols\md`). The text is [pandoc Markdown](https://pandoc.org/MANUAL.html#pandocs-markdown), one paragraph per line.
2. Rebuild the page: `python3 tools/build.py md/NNN-name.md` (or `python3 tools/build.py` to rebuild everything, needed after adding a protocol or changing a title, a category or a `public:` line).
3. Check it locally: `python3 -m http.server 8000`, then open http://localhost:8000.
4. Commit the Markdown file and the generated `docs/` files and `index.html`, then push.

The YAML header at the top of each file:

```yaml
---
title: "Apptainer"                            # page title
protocol: 135                                 # protocol number
revision: 0
date: 2026-11-20                              # last update, update it when editing
category: "Clusters, containers and workflows" # group of the menu and of the home page
draft: true                                   # optional: flagged as draft
numbersections: false                         # optional: no section numbers
public: true                                  # optional: also on the public website (default: private)
source: "Protocol 135-rev0 APPTAINER.docx"    # original Word file
---
```

Conventions:

| What | Markdown |
| --- | --- |
| Section, sub-section | `# Title`, `## Title` (numbers are added automatically) |
| Code block (copy button on the site) | three backticks, optionally followed by the language: ` ```bash `, ` ```r `, ` ```python ` |
| Command in a sentence | `` `ls -la` `` |
| Web link | `<https://www.example.org>` or `[text](https://www.example.org)` (a bare URL is not a link) |
| Link to another protocol | `[protocol 152](152-dsl2.md)` |
| Link to a section | `[Installation](#installation)` |
| Image | `![](img/135-apptainer/image1.png){width="60%"}` (files in `md/img/NNN-name/`) |
| Text in red, highlighted | `[warning]{.red}`, `[important]{.mark}` |
| Table | pipe table (lines such as `\| a \| b \|`) |
| Equation | `$x^2$`, `$$\frac{a}{b}$$` |

The categories (order of the menu) are listed in `CATEGORIES` at the top of `tools/build.py`.


<br /><br />
## BUILDING THE WEBSITE

Requirements: python 3 and [pandoc](https://pandoc.org) >= 3.1 (`sudo apt install pandoc` in WSL/Ubuntu; on WSL, the `pandoc.exe` bundled with RStudio is also found).

```bash
python3 tools/build.py                       # all the pages
python3 tools/build.py md/135-apptainer.md   # only this page (+ home page and search index)
```

Outputs: `docs/NNN-name.html` (one page per protocol), `index.html` (home page), `docs/search.html` and `docs/search_index.json` (search). The style is in `docs/assets/` (Read the Docs theme, as in [saferMake](https://github.com/safer-r/saferMake), plus `protocols.css` and `protocols.js`). The page template is `tools/template.html`. The links to the repository (Markdown source, licence) come from `site.json`.


<br /><br />
## ADDING A PROTOCOL

Either write a new `md/NNN-name.md` file with the YAML header above, or import a Word document:

```bash
python3 tools/docx2md.py "/mnt/c/Users/gmillot/Documents/Manip/Protocoles bioinfo/Protocol 187-rev0 NAME.docx"
python3 tools/build.py
```

The import keeps the conventions of the Word protocols: paragraphs in a monospace font become code blocks, monospace words inline code, red text `{.red}`, highlighted text `{.mark}`, tab-aligned lines tables, indented paragraphs block quotes; the Word table of contents is removed and the images are extracted into `md/img/NNN-name/` (EMF/WMF drawings converted to PNG with Windows PowerShell, big screenshots shrunk). Each Markdown file is then read back the way the website reads it, and a warning is printed if its text differs from the Word content.

A new protocol is not published until `public: true` is added to its YAML header.




<br /><br />
## REPOSITORY CONTENT

| File or folder | Description |
| --- | --- |
| `md/` | The protocols (Markdown, to edit) and their images (`md/img/`) |
| `index.html` | Home page of the website (generated) |
| `docs/` | Pages of the website (generated), style and scripts (`docs/assets/`) |
| `docs/Protocol ... .html(.htm)` | Redirections from the former page addresses |
| `site.json` | Links of the repository used by the website |
| `tools/build.py` | Builds the website from `md/` |
| `tools/template.html`, `tools/filters/site.lua` | Page template and pandoc filter of the website |
| `tools/docx2md.py`, `tools/docx_prep.py`, `tools/filters/docx_import.lua`, `tools/img_convert.ps1` | Import of Word protocols |
| `tools/export_public.py` | Copies the protocols marked `public: true` to another folder and builds their website |
| `docs/assets/COPYRIGHTS` | Licences of the bundled theme and fonts |



<br /><br />
## VERSIONS


The different releases are tagged [here](https://github.com/gael-millot/protocols/tags)

<br /><br />
## LICENCE


This package of scripts can be redistributed and/or modified under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
Distributed in the hope that it will be useful, but without any warranty; without even the implied warranty of merchantability or fitness for a particular purpose.
See the GNU General Public License for more details at https://www.gnu.org/licenses or in the Licence.txt attached file.

The website bundles the Read the Docs theme (MIT) and the Lato (OFL), Roboto Slab (Apache 2.0) and Font Awesome (OFL) fonts: see `docs/assets/COPYRIGHTS`.


<br /><br />
## CREDITS

[Gael A. Millot](https://github.com/gael-millot), Hub, Institut Pasteur, Paris, France


<br /><br />
## WHAT'S NEW IN

### v2.1

- Public and private protocols: only the protocols marked `public: true` are published
- Links of the repository read from `site.json`

### v2.0

- All the Word protocols converted into Markdown files (`md/`), easy to edit
- New website with the Read the Docs style: menu by category, home catalogue, full-text search, copy buttons, numbered sections
- Former page addresses redirected to the new pages

### v1.0

- Everything
