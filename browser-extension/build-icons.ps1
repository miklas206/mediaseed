# Windows PNG export of the existing code-native mediaseed.svg geometry.
# Runtime packages use the committed PNGs; Python builds need no graphics dependency.
Add-Type -AssemblyName System.Drawing
$target = Join-Path $PSScriptRoot 'icons'
New-Item -ItemType Directory -Force -Path $target | Out-Null
$canvas = [Drawing.Bitmap]::new(384,384)
$graphics = [Drawing.Graphics]::FromImage($canvas)
$graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.ScaleTransform(8,8)
$path = [Drawing.Drawing2D.GraphicsPath]::new()
$path.AddArc(1,1,24,24,180,90); $path.AddArc(23,1,24,24,270,90)
$path.AddArc(23,23,24,24,0,90); $path.AddArc(1,23,24,24,90,90); $path.CloseFigure()
$fill = [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml('#102d40'))
$graphics.FillPath($fill,$path)
$outline = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#263547'),1)
$graphics.DrawPath($outline,$path)
$cyan = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#25c9ed'),3)
$green = [Drawing.Pen]::new([Drawing.ColorTranslator]::FromHtml('#43d39b'),2.5)
foreach ($pen in @($cyan,$green)) { $pen.StartCap='Round'; $pen.EndCap='Round'; $pen.LineJoin='Round' }
$graphics.DrawLines($cyan,[Drawing.PointF[]]@([Drawing.PointF]::new(12,30),[Drawing.PointF]::new(12,17),[Drawing.PointF]::new(24,26),[Drawing.PointF]::new(36,17),[Drawing.PointF]::new(36,30)))
$graphics.DrawLine($cyan,24,12,24,26)
$graphics.DrawLines($green,[Drawing.PointF[]]@([Drawing.PointF]::new(19,33),[Drawing.PointF]::new(24,38),[Drawing.PointF]::new(29,33)))
$graphics.DrawLine($green,24,29,24,38)
foreach ($size in @(16,32,48,128)) {
    $image = [Drawing.Bitmap]::new($size,$size); $g = [Drawing.Graphics]::FromImage($image)
    $g.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($canvas,0,0,$size,$size)
    $image.Save((Join-Path $target "$size.png"),[Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $image.Dispose()
}
$graphics.Dispose(); $canvas.Dispose(); $fill.Dispose(); $outline.Dispose(); $cyan.Dispose(); $green.Dispose(); $path.Dispose()
