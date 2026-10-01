$ErrorActionPreference = 'Stop'

$studioRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$sourceImage = Join-Path $studioRoot 'assets\launcher\capybara.png'
$iconPath = Join-Path $studioRoot 'assets\launcher\studio.ico'
$launcherPath = Join-Path $PSScriptRoot 'Start-Studio.cmd'
$shortcutPath = Join-Path $studioRoot 'Start-Studio.lnk'

if (-not (Test-Path -LiteralPath $sourceImage -PathType Leaf)) { throw "Missing image: $sourceImage" }
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) { throw "Missing launcher: $launcherPath" }

Add-Type -AssemblyName System.Drawing
$source = [System.Drawing.Bitmap]::new($sourceImage)
try {
    $sizes = @(16, 24, 32, 48, 64, 128, 256)
    $images = New-Object System.Collections.Generic.List[byte[]]
    foreach ($size in $sizes) {
        $bitmap = [System.Drawing.Bitmap]::new($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $stream = [System.IO.MemoryStream]::new()
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $targetRect = [System.Drawing.Rectangle]::new(0, 0, $size, $size)
            $graphics.DrawImage($source, $targetRect, 0, 0, $source.Width, $source.Height, [System.Drawing.GraphicsUnit]::Pixel)
            if ($size -eq 256) {
                # Windows icon files commonly store the 256px image as PNG.
                $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
            } else {
                # Smaller entries use a 32-bit DIB plus the required AND mask.
                $writer = [System.IO.BinaryWriter]::new($stream)
                $maskStride = [int]([Math]::Ceiling($size / 32.0) * 4)
                $writer.Write([uint32]40)
                $writer.Write([int32]$size)
                $writer.Write([int32]($size * 2))
                $writer.Write([uint16]1)
                $writer.Write([uint16]32)
                $writer.Write([uint32]0)
                $writer.Write([uint32]($size * $size * 4 + $maskStride * $size))
                $writer.Write([int32]0)
                $writer.Write([int32]0)
                $writer.Write([uint32]0)
                $writer.Write([uint32]0)
                for ($y = $size - 1; $y -ge 0; $y--) {
                    for ($x = 0; $x -lt $size; $x++) {
                        $pixel = $bitmap.GetPixel($x, $y)
                        $writer.Write([byte]$pixel.B)
                        $writer.Write([byte]$pixel.G)
                        $writer.Write([byte]$pixel.R)
                        $writer.Write([byte]$pixel.A)
                    }
                }
                for ($y = $size - 1; $y -ge 0; $y--) {
                    $row = New-Object byte[] $maskStride
                    for ($x = 0; $x -lt $size; $x++) {
                        if ($bitmap.GetPixel($x, $y).A -eq 0) {
                            $byteIndex = $x -shr 3
                            $row[$byteIndex] = [byte]($row[$byteIndex] -bor (0x80 -shr ($x % 8)))
                        }
                    }
                    $writer.Write($row)
                }
            }
            $images.Add($stream.ToArray())
        } finally {
            $stream.Dispose()
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }

    $output = [System.IO.File]::Create($iconPath)
    $writer = [System.IO.BinaryWriter]::new($output)
    try {
        $writer.Write([uint16]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]$sizes.Count)
        $offset = 6 + 16 * $sizes.Count
        for ($i = 0; $i -lt $sizes.Count; $i++) {
            $size = $sizes[$i]
            $writer.Write([byte]($size % 256))
            $writer.Write([byte]($size % 256))
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$images[$i].Length)
            $writer.Write([uint32]$offset)
            $offset += $images[$i].Length
        }
        foreach ($bytes in $images) { $writer.Write($bytes) }
    } finally {
        $writer.Dispose()
    }
} finally {
    $source.Dispose()
}

$shell = New-Object -ComObject WScript.Shell
try {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcherPath
    $shortcut.WorkingDirectory = $studioRoot
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Description = 'Mo AI Animation Studio tren may nay'
    $shortcut.Save()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut)
} finally {
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}

Write-Host "Created shortcut: $shortcutPath"
Write-Host "Icon: $iconPath"
