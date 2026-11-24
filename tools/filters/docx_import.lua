--[[
docx_import.lua - pandoc Lua filter used by tools/docx2md.py (docx -> Markdown import).

Input: AST read with `-f docx+styles` from a docx pre-processed by tools/docx_prep.py.
Metadata expected (set with -M by docx2md.py):
  import_number, import_revision, import_name, import_source, import_date, import_textwidth (inches)

Actions:
  - drop the Word table of contents, unwrap the custom-style divs/spans of `+styles`
    (keeping "Proto Red" -> [..]{.red} and "Proto Mark" -> [..]{.mark});
  - tab-aligned lines (marked with U+21E5 by docx_prep.py) -> tables, else plain spaces;
  - take the title block ("PROTOCOL NN" + title) out of the body into the YAML front matter;
  - remove manual numbering typed in headings (numbers are added at build time);
  - clean inlines (adjacent code, empty emphasis, useless anchors, trailing line breaks);
  - links: local file links -> code, "protocol NN" file links -> protocol:NN (resolved by docx2md.py);
    addresses typed after words or spaces ("see%20http:/x.org") repaired, words out of the link;
    addresses without host ("/download/x") -> text, with a warning;
  - image size as a percentage of the Word text width, no height, no automatic alt text;
  - tables with one-line cells written as pipe tables (column widths dropped);
  - code blocks: non-breaking spaces, trailing spaces, blank lines.
]]

local stringify = pandoc.utils.stringify
local TAB = "\u{21E5}"

local function meta_str(meta, key)
  local v = meta[key]
  if v == nil then return nil end
  return stringify(v)
end

-------------------------------------------------------------------------------
-- custom styles
-------------------------------------------------------------------------------
local function is_toc_style(cs)
  local l = cs:lower()
  return l:match("^toc") ~= nil or l:match("table of contents") ~= nil
      or l:match("^tm%d") ~= nil or l:match("en%-t[eê]te de table des mati") ~= nil
      or l:match("^index") ~= nil
end

local pass_styles = {
  Div = function(el)
    local cs = el.attributes["custom-style"]
    if cs then
      if is_toc_style(cs) then return {} end
      return el.content
    end
  end,
  Span = function(el)
    local cs = el.attributes["custom-style"]
    if cs == "Proto Red" then
      return pandoc.Span(el.content, pandoc.Attr("", {"red"}))
    elseif cs == "Proto Mark" then
      return pandoc.Span(el.content, pandoc.Attr("", {"mark"}))
    elseif cs then
      return el.content
    end
  end,
}

-------------------------------------------------------------------------------
-- tab-aligned lines -> tables
-------------------------------------------------------------------------------
local function is_blank_inline(il)
  return il.t == "Space" or il.t == "SoftBreak" or il.t == "LineBreak"
      or (il.t == "Str" and il.text:match("^[%s\194\160]*$"))
end

local function trim_inlines(inlines)
  while #inlines > 0 and is_blank_inline(inlines[1]) do inlines:remove(1) end
  while #inlines > 0 and is_blank_inline(inlines[#inlines]) do inlines:remove(#inlines) end
  return inlines
end

local function has_tab(blk)
  if blk.t ~= "Para" and blk.t ~= "Plain" then return false end
  for _, il in ipairs(blk.content) do
    if il.t == "Str" and il.text:find(TAB, 1, true) then return true end
  end
  return false
end

-- split a Str that contains markers into Str / separator tokens
local function explode(inlines)
  local out = pandoc.List()
  for _, il in ipairs(inlines) do
    if il.t == "Str" and il.text:find(TAB, 1, true) then
      local s = il.text
      while true do
        local a, b = s:find(TAB, 1, true)
        if not a then break end
        if a > 1 then out:insert(pandoc.Str(s:sub(1, a - 1))) end
        out:insert("SEP")
        s = s:sub(b + 1)
      end
      if s ~= "" then out:insert(pandoc.Str(s)) end
    else
      out:insert(il)
    end
  end
  return out
end

local function split_cells(inlines)
  local cells = pandoc.List({pandoc.List()})
  for _, il in ipairs(explode(inlines)) do
    if il == "SEP" then
      if #trim_inlines(cells[#cells]) > 0 then cells:insert(pandoc.List()) end
    else
      cells[#cells]:insert(il)
    end
  end
  if #trim_inlines(cells[#cells]) == 0 then cells:remove(#cells) end
  return cells
end

local function untab(inlines)
  -- markers -> single spaces
  local out = pandoc.List()
  for _, il in ipairs(explode(inlines)) do
    if il == "SEP" then
      if #out > 0 and out[#out].t ~= "Space" then out:insert(pandoc.Space()) end
    elseif il.t == "Space" and #out > 0 and out[#out].t == "Space" then
    else
      out:insert(il)
    end
  end
  return out
end

local function all_strong(inlines)
  local seen = false
  for _, il in ipairs(inlines) do
    if il.t == "Strong" then seen = true
    elseif not is_blank_inline(il) then return false end
  end
  return seen
end

local function strong_content(inlines)
  local out = pandoc.List()
  for _, il in ipairs(inlines) do
    if il.t == "Strong" then out:extend(il.content) else out:insert(il) end
  end
  return trim_inlines(out)
end

local function make_table(rows)
  local ncol = 0
  for _, r in ipairs(rows) do ncol = math.max(ncol, #r) end
  local header = {}
  local first = 1
  local is_header = true
  for _, c in ipairs(rows[1]) do
    if not all_strong(c) then is_header = false end
  end
  if is_header and #rows > 2 then
    for j = 1, ncol do
      header[j] = {pandoc.Plain(rows[1][j] and strong_content(rows[1][j]) or {})}
    end
    first = 2
  end
  local body = {}
  for i = first, #rows do
    local r = {}
    for j = 1, ncol do r[j] = {pandoc.Plain(rows[i][j] or {})} end
    body[#body + 1] = r
  end
  local aligns, widths = {}, {}
  for j = 1, ncol do aligns[j] = pandoc.AlignDefault; widths[j] = 0 end
  return pandoc.utils.from_simple_table(pandoc.SimpleTable({}, aligns, widths, header, body))
end

local function convert_group(group)
  local rows = {}
  local ok = #group >= 2
  for _, blk in ipairs(group) do
    local cells = split_cells(blk.content)
    if #cells < 2 or #cells > 8 or #stringify(cells[1]) > 80 then ok = false end
    rows[#rows + 1] = cells
  end
  if ok then return {make_table(rows)} end
  local out = {}
  for _, blk in ipairs(group) do
    blk.content = untab(blk.content)
    out[#out + 1] = blk
  end
  return out
end

local pass_tabs = {
  traverse = "topdown",
  Table = function(tbl)
    -- no table inside a table: markers -> spaces
    return tbl:walk({Inlines = function(ils) return untab(ils) end}), false
  end,
  Blocks = function(blocks)
    local out = pandoc.List()
    local group = {}
    local function flush()
      if #group > 0 then out:extend(convert_group(group)) end
      group = {}
    end
    for _, blk in ipairs(blocks) do
      if has_tab(blk) then
        group[#group + 1] = blk
      else
        flush()
        out:insert(blk)
      end
    end
    flush()
    return out
  end,
}

local pass_untab = {
  Inlines = function(ils) return untab(ils) end,
}

-------------------------------------------------------------------------------
-- inline cleaning
-------------------------------------------------------------------------------
local function merge_inlines(inlines)
  -- merge adjacent Code (also Code Space Code), drop empty emphasis
  local out = pandoc.List()
  for _, il in ipairs(inlines) do
    local prev = out[#out]
    local fmt = il.t == "Strong" or il.t == "Emph" or il.t == "Underline" or il.t == "Strikeout"
        or (il.t == "Span" and #il.classes > 0 and il.identifier == "")
    local blank_only = fmt and #il.content > 0
    if blank_only then
      for _, x in ipairs(il.content) do
        if not is_blank_inline(x) then blank_only = false break end
      end
    end
    if fmt and #il.content == 0 then
      -- drop empty formatting
    elseif blank_only then
      -- formatting around spaces or line breaks only (written **\** otherwise): a space
      if prev and prev.t ~= "Space" then out:insert(pandoc.Space()) end
    elseif il.t == "Code" and prev and prev.t == "Code" then
      prev.text = prev.text .. il.text
    elseif il.t == "Code" and prev and prev.t == "Space" and out[#out - 1] and out[#out - 1].t == "Code" then
      out:remove(#out)
      out[#out].text = out[#out].text .. " " .. il.text
    elseif (il.t == "Strong" or il.t == "Emph" or il.t == "Underline") and prev and prev.t == il.t then
      prev.content:extend(il.content)
    elseif il.t == "Span" and prev and prev.t == "Span" and #il.classes == 1 and #prev.classes == 1
        and il.classes[1] == prev.classes[1] and il.identifier == "" and prev.identifier == "" then
      prev.content:extend(il.content)
    else
      out:insert(il)
    end
  end
  -- Code: move leading/trailing spaces outside the code span
  local res = pandoc.List()
  for _, il in ipairs(out) do
    if il.t == "Code" then
      local lead, body, trail = il.text:match("^([%s\194\160]*)(.-)([%s\194\160]*)$")
      if lead ~= "" and #res > 0 and res[#res].t ~= "Space" then res:insert(pandoc.Space()) end
      if body ~= "" then
        il.text = body
        res:insert(il)
        if trail ~= "" then res:insert(pandoc.Space()) end
      end
    else
      res:insert(il)
    end
  end
  return res
end

local pass_inlines = {
  Inlines = merge_inlines,
}

-------------------------------------------------------------------------------
-- anchors (keep only the ones that are link targets) and links
-------------------------------------------------------------------------------
local link_targets = {}

local pass_collect_links = {
  Link = function(el)
    local t = el.target
    if t:sub(1, 1) == "#" then link_targets[t:sub(2)] = true end
  end,
}

-- A link address typed in Word after words or spaces ("see http://x.org", " https://x.org",
-- "(https://x.org", "See also C:\dir\f.docx") is stored as a relative path: words kept, spaces
-- as %20, "\" as "/" and the "//" of the URL collapsed ("see%20http:/x.org",
-- "See%20also%20C:/dir/f.docx"), a broken link on the website.
-- Returns the words (decoded, may be "") and the repaired address, or nil.
local function typed_address(t)
  local low = t:lower()
  local a, b = low:find("%f[%a]https?:/+")
  if not a then a, b = low:find("%f[%a]ftp:/+") end
  local addr
  if a then
    local colon = low:find(":", a, true)
    if a == 1 and b - colon == 2 then return nil end -- well-formed URL
    addr = t:sub(a, colon) .. "//" .. t:sub(b + 1)
  else
    a = t:find("%f[%a]%a:/") -- local file (Windows drive)
    if not a then return nil end
    addr = "file:///" .. t:sub(a)
  end
  local words = t:sub(1, a - 1):gsub("%%20", " ")
  if not words:match("^[%a%s%(:,;%.%-]*$") then return nil end -- part of a path or a query
  return words, addr
end

local function text_inlines(s)
  local out = pandoc.List()
  for sp, word in s:gmatch("(%s*)(%S*)") do
    if sp ~= "" then out:insert(pandoc.Space()) end
    if word ~= "" then out:insert(pandoc.Str(word)) end
  end
  return out
end

local pass_word_links = {
  Link = function(el)
    local t = el.target:gsub("^%s+", ""):gsub("%s+$", "")
    -- address of a copied web page, relative to its site ("/download/x"): no host to link to
    if t:match("^/[^/]") then
      io.stderr:write("link without host kept as text: [" .. stringify(el.content) .. "](" .. t .. ")\n")
      return el.content
    end
    local words, addr = typed_address(t)
    if not addr then return nil end
    el.target = addr -- local files: then handled as the file: links (pass_anchors)
    -- the words typed before the address, also at the start of the link text: out of the link
    local w, txt = words:gsub("^%s+", ""), stringify(el.content)
    local rest = txt:sub(#w + 1):gsub("^%s+", "")
    if w == "" or txt:sub(1, #w) ~= w or rest == "" then return el end
    el.content = text_inlines(rest)
    local out = text_inlines(w)
    out:insert(el)
    return out
  end,
}

local pass_math_links = {
  -- a blank line inside display math ends the Markdown paragraph (and is invalid LaTeX)
  Math = function(el)
    el.text = el.text:gsub("\n[ \t]*\n", "\n")
    return el
  end,
  Inlines = function(inlines)
    local out, changed = pandoc.List(), false
    for _, il in ipairs(inlines) do
      if il.t == "Math" and il.mathtype == "InlineMath" then
        local t, lead, trail = il.text, false, false
        while true do
          local t2 = t:gsub("^%s+", ""):gsub("^\\[ ,;:!]", "")
          if t2 == t then break end
          t, lead = t2, true
        end
        while true do
          -- trailing spaces, then \, \; \: \! or the backslash of a "\ " whose space was removed
          local t2 = t:gsub("%s+$", ""):gsub("\\[,;:!]$", ""):gsub("\\$", "")
          if t2 == t then break end
          t, trail = t2, true
        end
        if lead or trail then changed = true end
        if lead and #out > 0 and out[#out].t ~= "Space" then out:insert(pandoc.Space()) end
        if t ~= "" then
          il.text = t
          out:insert(il)
        end
        if trail then out:insert(pandoc.Space()) end
      else
        out:insert(il)
      end
    end
    if changed then return out end
  end,
  -- Word link targets such as "(https:/x.org/" break the Markdown link syntax
  Link = function(el)
    local t = el.target:gsub("^%s+", ""):gsub("%s+$", "")
    if t:match("^%(%a+:") then t = t:sub(2) end
    if t:match("^%a[%w+.-]*://") then t = t:gsub("%(", "%%28"):gsub("%)", "%%29") end
    el.target = t
    return el
  end,
}

local pass_anchors = {
  Span = function(el)
    if el.identifier ~= "" and #el.content == 0 and #el.classes <= 1 then
      if link_targets[el.identifier] then
        return pandoc.Span({}, pandoc.Attr(el.identifier))
      end
      return {}
    end
    if el.classes:includes("anchor") then
      el.classes = el.classes:filter(function(c) return c ~= "anchor" end)
      if not link_targets[el.identifier] then el.identifier = "" end
      if el.identifier == "" and #el.classes == 0 and next(el.attributes) == nil then
        return el.content
      end
      return el
    end
  end,
  Link = function(el)
    local t = el.target
    if t:match("^https?://") and t:find("%%23") then
      el.target = t:gsub("%%23", "#")
      return el
    end
    if t:match("^[Ff][Ii][Ll][Ee]:") then
      local txt = stringify(el.content)
      local num = txt:lower():match("protocol%s*(%d+)")
      if num then
        el.target = "protocol:" .. num
        return el
      end
      if txt:find("[/\\]") then return pandoc.Code(txt) end
      return el.content
    end
  end,
}

-------------------------------------------------------------------------------
-- plain-text URLs -> explicit links <https://...> (written verbatim by pandoc; escaped characters
-- inside a bare URL would otherwise break autolink_bare_uris when the site is built)
-------------------------------------------------------------------------------
local function url_split(s)
  -- first URL of a text: pre, url, rest (the rest may contain other URLs)
  local a = s:find("https?://") or s:find("ftp://")
  if not a then return nil end
  local pre, rest = s:sub(1, a - 1), s:sub(a)
  -- the URL stops at a non-breaking space (and other Latin-1 punctuation) or a character
  -- that cannot be in a URL
  local stop = rest:find("[\194<>\"`{}|\\^%[%]]")
  local url, tail = rest, ""
  if stop then url, tail = rest:sub(1, stop - 1), rest:sub(stop) end
  while #url > 0 do
    local last = url:sub(-1)
    if not last:match("[%.,;:!%?%)'\"]") then break end
    if last == ")" then
      local opens = select(2, url:gsub("%(", ""))
      local closes = select(2, url:gsub("%)", ""))
      if closes <= opens then break end
    end
    tail = last .. tail
    url = url:sub(1, -2)
  end
  if #url < 12 then return nil end
  return pre, url, tail
end

local pass_urls = {
  -- bottom-up, each inline list once (a top-down walk would re-visit the new links forever)
  Inlines = function(inlines)
    local out, changed = pandoc.List(), false
    for _, il in ipairs(inlines) do
      local text = il.t == "Str" and il.text or nil
      local pre, url, tail = nil, nil, nil
      if text then pre, url, tail = url_split(text) end
      if url then
        changed = true
        while url do
          if pre ~= "" then out:insert(pandoc.Str(pre)) end
          out:insert(pandoc.Link({pandoc.Str(url)}, url))
          text = tail
          pre, url, tail = url_split(text)
        end
        if text ~= "" then out:insert(pandoc.Str(text)) end
      else
        out:insert(il)
      end
    end
    if changed then return out end
  end,
}

local pass_unnest_links = {
  -- URLs that were already link texts, or in image captions: no link inside a link or a caption
  Link = function(el)
    el.content = el.content:walk({Link = function(inner) return inner.content end})
    return el
  end,
  Image = function(img)
    img.caption = img.caption:walk({Link = function(l) return l.content end})
    return img
  end,
  Figure = function(fig)
    fig.caption.long = fig.caption.long:walk({Link = function(l) return l.content end})
    return fig
  end,
}

-------------------------------------------------------------------------------
-- blocks (headings, empty paragraphs, code, images, tables)
-------------------------------------------------------------------------------
local textwidth = 6.3

local function strip_heading_number(inlines)
  -- "1. Aim", "2.1. Linux", "3) Foo", "1.Aim" typed by hand in the heading
  if #inlines == 0 or inlines[1].t ~= "Str" then return inlines end
  local s = inlines[1].text
  local rest = s:match("^%d+[%.%d]*[%.%)]%s*(.*)$")
  if rest == nil then return inlines end
  -- a plain number such as "16S" or "2020" is kept (needs a dot or parenthesis)
  if rest == "" then
    inlines:remove(1)
  else
    inlines[1] = pandoc.Str(rest)
  end
  return trim_inlines(inlines)
end

local function unwrap_strong(inlines)
  if #inlines == 1 and (inlines[1].t == "Strong") then return inlines[1].content end
  return inlines
end

local function to_inches(v)
  if not v then return nil end
  local n, unit = tostring(v):match("^([%d%.]+)%s*(%a*)$")
  n = tonumber(n)
  if not n then return nil end
  if unit == "in" or unit == "" then return n end
  if unit == "cm" then return n / 2.54 end
  if unit == "mm" then return n / 25.4 end
  if unit == "px" then return n / 96 end
  if unit == "pt" then return n / 72 end
  return nil
end

local function auto_alt(alt)
  local l = alt:lower()
  return alt:match("%.%a%a%a%a?$") or l:match("^image%s*%d*$") or l:match("^picture%s*%d*$")
      or l:match("^description") or l:find("automatically generated", 1, true)
      or l:find("automatiquement", 1, true) or l:match("^une image contenant")
      or l:match("^a screenshot") or l:match("^a picture containing") or #alt > 150
      or l:match("^%a+://%S+$")
end

local function simple_cell(cell)
  if (cell.row_span or 1) > 1 or (cell.col_span or 1) > 1 then return false end
  local c = cell.contents
  if #c > 1 then return false end
  if #c == 1 then
    if c[1].t ~= "Plain" and c[1].t ~= "Para" then return false end
    for _, il in ipairs(c[1].content) do
      if il.t == "LineBreak" or il.t == "SoftBreak" or il.t == "Image" or il.t == "Note" then return false end
    end
  end
  return true
end

local function simple_table(tbl)
  local rows = {}
  for _, r in ipairs(tbl.head.rows) do rows[#rows + 1] = r end
  for _, b in ipairs(tbl.bodies) do
    if #b.head > 0 then return false end
    for _, r in ipairs(b.body) do rows[#rows + 1] = r end
  end
  if #tbl.foot.rows > 0 then return false end
  for _, r in ipairs(rows) do
    for _, cell in ipairs(r.cells) do
      if not simple_cell(cell) then return false end
    end
  end
  return true
end

local pass_blocks = {
  Header = function(el)
    local c = trim_inlines(el.content)
    c = strip_heading_number(c)
    c = unwrap_strong(c)
    c = c:filter(function(il) return il.t ~= "LineBreak" end)
    if #c == 0 then return {} end
    el.content = c
    return el
  end,
  Para = function(el)
    local c = trim_inlines(el.content)
    local out = pandoc.List()
    for _, il in ipairs(c) do
      if il.t == "LineBreak" and out[#out] and out[#out].t == "LineBreak" then
      else out:insert(il) end
    end
    if #out == 0 then return {} end
    el.content = out
    return el
  end,
  Plain = function(el)
    el.content = trim_inlines(el.content)
    if #el.content == 0 then return {} end
    return el
  end,
  CodeBlock = function(el)
    local t = el.text:gsub("\194\160", " "):gsub("\r", "")
    local lines = pandoc.List()
    for line in (t .. "\n"):gmatch("(.-)\n") do
      lines:insert((line:gsub("[ \t]+$", "")))
    end
    while #lines > 0 and lines[1] == "" do lines:remove(1) end
    while #lines > 0 and lines[#lines] == "" do lines:remove(#lines) end
    if #lines == 0 then return {} end
    el.text = table.concat(lines, "\n")
    -- the class only makes pandoc write a fenced block; docx2md.py removes it afterwards
    el.attr = pandoc.Attr("", {"importcode"}, {})
    return el
  end,
  Image = function(el)
    local w = to_inches(el.attributes["width"])
    el.attributes["height"] = nil
    el.attributes["width"] = nil
    if w and textwidth > 0 then
      local pct = math.floor(w / textwidth * 100 + 0.5)
      if pct < 95 then
        el.attributes["width"] = tostring(math.max(pct, 5)) .. "%"
      end
    end
    if auto_alt(stringify(el.caption)) then el.caption = {} end
    el.title = ""
    return el
  end,
  Figure = function(fig)
    -- a figure caption made from an automatic Word alt text is not a caption
    if auto_alt(stringify(fig.caption.long)) then fig.caption.long = pandoc.Blocks({}) end
    return fig
  end,
  Table = function(tbl)
    if simple_table(tbl) then
      for i, cs in ipairs(tbl.colspecs) do
        tbl.colspecs[i] = {cs[1], pandoc.ColWidthDefault}
      end
    end
    return tbl
  end,
}

-------------------------------------------------------------------------------
-- title block -> metadata
-------------------------------------------------------------------------------
local function first_text_blocks(blocks, n)
  local idx = pandoc.List()
  for i, b in ipairs(blocks) do
    if b.t == "Header" or b.t == "Table" or b.t == "CodeBlock" or b.t == "BulletList"
        or b.t == "OrderedList" then break end
    if (b.t == "Para" or b.t == "Plain") and stringify(b):match("%S") then idx:insert(i) end
    if #idx >= n then break end
  end
  return idx
end

local function title_block(doc)
  local blocks = doc.blocks
  local idx = first_text_blocks(blocks, 3)
  local title = nil
  local remove = {}
  for k, i in ipairs(idx) do
    local s = stringify(blocks[i])
    if s:upper():match("^%s*%a*PROTOCOL%s+%d+%s*$") then
      remove[i] = true
      local j = idx[k + 1]
      if j and #stringify(blocks[j]) <= 120 then
        title = stringify(blocks[j])
        remove[j] = true
      end
      break
    end
  end
  if not title and #idx >= 1 then
    -- no "PROTOCOL NN" line: a first short line followed by a heading is the title
    local i = idx[1]
    local s = stringify(blocks[i])
    local nxt = blocks[i + 1]
    if #s <= 80 and nxt and nxt.t == "Header" then
      title = s
      remove[i] = true
    end
  end
  local newblocks = pandoc.List()
  for i, b in ipairs(blocks) do
    if not remove[i] then newblocks:insert(b) end
  end
  doc.blocks = newblocks
  return title
end

-------------------------------------------------------------------------------
-- internal links written with a Word section number ("section 6.6") -> heading title
-------------------------------------------------------------------------------
local headers = {}

local pass_collect_headers = {
  Header = function(el)
    if el.identifier ~= "" then headers[el.identifier] = el.content end
  end,
}

local pass_xref = {
  Link = function(el)
    local t = el.target
    if t:sub(1, 1) == "#" and headers[t:sub(2)] then
      local txt = stringify(el.content)
      if txt:match("^%s*%d+[%.%d]*%s*$") then
        el.content = headers[t:sub(2)]:walk({
          Link = function(l) return l.content end,
          Note = function() return {} end,
        })
        return el
      end
    end
  end,
}

-------------------------------------------------------------------------------
local pass_empty = {
  BlockQuote = function(el) if #el.content == 0 then return {} end end,
  Div = function(el) if #el.content == 0 then return {} end end,
}

function Pandoc(doc)
  local meta = doc.meta
  textwidth = tonumber(meta_str(meta, "import_textwidth") or "") or 6.3

  doc = doc:walk(pass_styles)
  doc = doc:walk(pass_tabs)
  doc = doc:walk(pass_untab)
  doc = doc:walk(pass_inlines)
  doc:walk(pass_collect_links)
  doc = doc:walk(pass_word_links)
  doc = doc:walk(pass_anchors)
  doc = doc:walk(pass_math_links)
  doc = doc:walk(pass_urls)
  doc = doc:walk(pass_unnest_links)
  doc = doc:walk(pass_blocks)
  doc:walk(pass_collect_headers)
  doc = doc:walk(pass_xref)
  doc = doc:walk(pass_inlines)
  doc = doc:walk(pass_empty)

  local title = title_block(doc)
  local name = meta_str(meta, "import_name")
  if not title or not title:match("%S") then title = name end
  title = title:gsub("%s+", " "):gsub("^%s+", ""):gsub("%s+$", "")

  local newmeta = pandoc.Meta({})
  newmeta.title = pandoc.MetaString(title)
  newmeta.protocol = pandoc.MetaString(meta_str(meta, "import_number") or "")
  newmeta.revision = pandoc.MetaString(meta_str(meta, "import_revision") or "0")
  if meta_str(meta, "import_date") then newmeta.date = pandoc.MetaString(meta_str(meta, "import_date")) end
  newmeta.source = pandoc.MetaString(meta_str(meta, "import_source") or "")
  doc.meta = newmeta
  return doc
end
