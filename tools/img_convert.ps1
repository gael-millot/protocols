# img_convert.ps1 - image conversions used by tools/docx2md.py when run from WSL or Windows.
#
# Windows PowerShell (System.Drawing / GDI+) renders Word metafiles (EMF/WMF), which have no
# browser support, and shrinks oversized screenshots.
#
# Usage: powershell.exe -NoProfile -ExecutionPolicy Bypass -File img_convert.ps1 -List <jobs.tsv>
# Each line of the jobs file: mode<TAB>input<TAB>output<TAB>maxwidth
#   mode "emf"    : render the metafile to PNG (white background), at most maxwidth pixels wide
#   mode "resize" : resize a PNG/JPEG/GIF to maxwidth pixels wide (same format)
# Paths are Windows paths (\\wsl.localhost\... works). One output line per job: OK|ERR <TAB> output

param([Parameter(Mandatory = $true)][string]$List)

Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = "Stop"

function Save-Bitmap($bmp, $out) {
    $ext = [System.IO.Path]::GetExtension($out).ToLower()
    if ($ext -eq ".jpg" -or $ext -eq ".jpeg") {
        $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
        $params = New-Object System.Drawing.Imaging.EncoderParameters(1)
        $params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]88)
        $bmp.Save($out, $codec, $params)
    } elseif ($ext -eq ".gif") {
        $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Gif)
    } else {
        $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    }
}

function Draw-Scaled($img, [int]$w, [int]$h, [bool]$white) {
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    if ($white) { $g.Clear([System.Drawing.Color]::White) }
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $g.DrawImage($img, 0, 0, $w, $h)
    $g.Dispose()
    return $bmp
}

foreach ($line in [System.IO.File]::ReadAllLines($List)) {
    if (-not $line.Trim()) { continue }
    $f = $line.Split("`t")
    $mode = $f[0]; $in = $f[1]; $out = $f[2]; $maxw = [int]$f[3]
    try {
        if ($mode -eq "emf") {
            $mf = New-Object System.Drawing.Imaging.Metafile($in)
            $hd = $mf.GetMetafileHeader()
            # physical size in inches -> 2x a 96 dpi screen, capped to maxwidth
            $win = $hd.Bounds.Width / [Math]::Max($hd.DpiX, 1)
            $hin = $hd.Bounds.Height / [Math]::Max($hd.DpiY, 1)
            if ($win -le 0 -or $hin -le 0) { $win = $mf.Width / 96.0; $hin = $mf.Height / 96.0 }
            $w = [int][Math]::Round($win * 192)
            if ($w -gt $maxw) { $w = $maxw }
            if ($w -lt 16) { $w = 16 }
            $h = [int][Math]::Round($w * $hin / $win)
            if ($h -lt 1) { $h = 1 }
            $bmp = Draw-Scaled $mf $w $h $true
            Save-Bitmap $bmp $out
            $bmp.Dispose(); $mf.Dispose()
        } elseif ($mode -eq "resize") {
            $bytes = [System.IO.File]::ReadAllBytes($in)
            $ms = New-Object System.IO.MemoryStream(, $bytes)
            $img = [System.Drawing.Image]::FromStream($ms)
            if ($img.Width -le $maxw) { $img.Dispose(); Write-Output "SKIP`t$out"; continue }
            $w = $maxw
            $h = [int][Math]::Round($img.Height * $maxw / $img.Width)
            $isJpeg = $img.RawFormat.Equals([System.Drawing.Imaging.ImageFormat]::Jpeg)
            $bmp = Draw-Scaled $img $w $h $isJpeg
            $img.Dispose(); $ms.Dispose()
            Save-Bitmap $bmp $out
            $bmp.Dispose()
        }
        Write-Output "OK`t$out"
    } catch {
        Write-Output ("ERR`t$out`t" + $_.Exception.Message)
    }
}
