--[[
site.lua - pandoc Lua filter used by tools/build.py (Markdown protocol -> HTML page of the site).

  - headings: the highest level used becomes level 2 (level 1 is the page title), numbered 1, 1.1,
    1.1.1 (front matter `numbersections: false` to disable), with a link anchor;
  - `sidetoc` metadata: table of contents of the page, inserted in the left menu by the template;
  - links to other protocols written `NNN-name.md` -> `NNN-name.html`;
  - relative image paths prefixed with the `imgprefix` metadata (pages are in docs/, images in md/img/);
  - tables wrapped for horizontal scrolling, empty header rows removed.
]]

local stringify = pandoc.utils.stringify

local function meta_str(meta, key)
  local v = meta[key]
  if v == nil then return nil end
  if type(v) == "boolean" then return tostring(v) end
  return stringify(v)
end

local function escape_html(s)
  return (s:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;"):gsub('"', "&quot;"))
end

local function inlines_html(inlines)
  -- heading text for the menu: no links, notes or anchors inside
  local clean = pandoc.Inlines(inlines):walk({
    Link = function(l) return l.content end,
    Note = function() return {} end,
    RawInline = function() return {} end,
    Span = function(s) if s.classes:includes("header-section-number") then return {} end end,
  })
  local html = pandoc.write(pandoc.Pandoc({pandoc.Plain(clean)}), "html")
  return (html:gsub("%s+$", ""))
end

local function is_relative(url)
  return not (url:match("^%a[%w+.-]*:") or url:match("^/") or url:match("^#"))
end

function Pandoc(doc)
  local meta = doc.meta
  local imgprefix = meta_str(meta, "imgprefix") or ""
  local numbering = meta_str(meta, "numbersections") ~= "false"

  -- 1) headings
  local minlevel = 99
  doc:walk({Header = function(h) if h.level < minlevel then minlevel = h.level end end})
  local shift = (minlevel == 99) and 0 or (1 - minlevel)
  local counters = {0, 0, 0, 0, 0, 0}
  local toc = {}
  doc = doc:walk({
    Header = function(h)
      local L = h.level + shift
      local num = nil
      if numbering and L <= 3 then
        for k = 1, L - 1 do if counters[k] == 0 then counters[k] = 1 end end
        counters[L] = counters[L] + 1
        for k = L + 1, 6 do counters[k] = 0 end
        local parts = {}
        for k = 1, L do parts[#parts + 1] = tostring(counters[k]) end
        num = table.concat(parts, ".")
      end
      if L <= 3 and h.identifier ~= "" then
        toc[#toc + 1] = {level = L, id = h.identifier, num = num, html = inlines_html(h.content)}
      end
      if num then
        h.content:insert(1, pandoc.Space())
        h.content:insert(1, pandoc.Span({pandoc.Str(num)}, pandoc.Attr("", {"header-section-number"})))
      end
      if h.identifier ~= "" then
        h.content:insert(pandoc.RawInline("html",
          '<a class="headerlink" href="#' .. escape_html(h.identifier) .. '" title="Link to this heading"></a>'))
      end
      h.level = math.min(L + 1, 6)
      return h
    end,
  })

  -- 2) table of contents for the left menu
  if #toc > 0 then
    local out = {'<ul class="page-toc">'}
    local depth, li_open = 1, false
    for i, e in ipairs(toc) do
      local lvl = math.min(e.level, depth + 1)
      if i == 1 then lvl = 1 end
      if lvl > depth then
        out[#out + 1] = "<ul>"          -- nested list inside the open item
        depth = lvl
      else
        if li_open then out[#out + 1] = "</li>" end
        while depth > lvl do
          out[#out + 1] = "</ul></li>"
          depth = depth - 1
        end
      end
      local label = (e.num and ('<span class="sec-num">' .. e.num .. "</span>") or "") .. e.html
      out[#out + 1] = '<li class="toctree-l' .. (lvl + 1) .. '"><a class="reference internal" href="#'
          .. escape_html(e.id) .. '">' .. label .. "</a>"
      li_open = true
    end
    if li_open then out[#out + 1] = "</li>" end
    while depth > 1 do
      out[#out + 1] = "</ul></li>"
      depth = depth - 1
    end
    out[#out + 1] = "</ul>"
    meta.sidetoc = pandoc.MetaInlines({pandoc.RawInline("html", table.concat(out, ""))})
  end

  -- 3) links, images, tables
  doc = doc:walk({
    Link = function(l)
      local t = l.target
      if is_relative(t) then
        local path, frag = t:match("^([^#]*)(#?.*)$")
        if path:match("%.md$") then
          l.target = path:gsub("%.md$", ".html") .. frag
          return l
        end
      end
    end,
    Image = function(img)
      if is_relative(img.src) then img.src = imgprefix .. img.src end
      img.attributes["loading"] = "lazy"
      return img
    end,
    Table = function(tbl)
      local empty = #tbl.head.rows > 0
      for _, row in ipairs(tbl.head.rows) do
        for _, cell in ipairs(row.cells) do
          if stringify(cell.contents):match("%S") then empty = false end
        end
      end
      if empty then tbl.head = pandoc.TableHead() end
      -- column widths decided by the browser from the content (no fixed percentages)
      for i, cs in ipairs(tbl.colspecs) do tbl.colspecs[i] = {cs[1], pandoc.ColWidthDefault} end
      return pandoc.Div({tbl}, pandoc.Attr("", {"wy-table-responsive"}))
    end,
  })
  doc.meta = meta
  return doc
end
