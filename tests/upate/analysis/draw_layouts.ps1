Add-Type -AssemblyName System.Drawing
$base=Join-Path $PSScriptRoot 'design_findings.json'
$data=Get-Content -Raw -LiteralPath $base | ConvertFrom-Json
$bmp=[System.Drawing.Bitmap]::new(1680,620)
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode=[System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::White)
$dark=[System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#182635'))
$blue=[System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#1767bc'))
$red=[System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#ca3645'))
$gray=[System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#d6dce2'),1)
$fTitle=[System.Drawing.Font]::new('Malgun Gothic',26,[System.Drawing.FontStyle]::Bold)
$fHead=[System.Drawing.Font]::new('Malgun Gothic',21,[System.Drawing.FontStyle]::Bold)
$fText=[System.Drawing.Font]::new('Malgun Gothic',15)
$fSmall=[System.Drawing.Font]::new('Malgun Gothic',12)
$g.DrawString('다대다 초기 배치: line · column · staggered',$fTitle,$dark,25,12)
$g.DrawString('실제 3대3 입력 좌표 | 점 하나 = 병사 한 명이 아니라 편제 하나의 중심 | BLUE →   ← RED',$fText,$dark,25,62)
$names=@('line · 횡대','column · 종대','staggered · 엇갈림')
for($i=0;$i -lt 3;$i++){
 $left=20+550*$i
 $d=$data.geometry[$i]
 $g.DrawString($names[$i],$fHead,$dark,$left+65,102)
 $g.DrawRectangle($gray,[single]($left+50),[single]145,[single]440,[single]220)
 foreach($t in @(-200,-100,0,100,200)){
  $x=$left+50+($t+250)*.88
  $g.DrawLine($gray,[single]$x,[single]145,[single]$x,[single]365)
  $g.DrawString([string]$t,$fSmall,$dark,[single]($x-18),[single]370)
 }
 foreach($t in @(-100,0,100)){
  $y=365-($t+125)*.88
  $g.DrawLine($gray,[single]($left+50),[single]$y,[single]($left+490),[single]$y)
  $g.DrawString([string]$t,$fSmall,$dark,[single]($left+8),[single]($y-10))
 }
 foreach($u in $d.units){
  $x=$left+50+($u.pos[0]-1750)*.88
  $y=365-($u.pos[1]-1875)*.88
  $brush=if($u.side -eq 'BLUE'){$blue}else{$red}
  $dir=if($u.side -eq 'BLUE'){1}else{-1}
  $g.FillEllipse($brush,[single]($x-7),[single]($y-7),[single]14,[single]14)
  $pen=[System.Drawing.Pen]::new($brush.Color,2)
  $pen.EndCap=[System.Drawing.Drawing2D.LineCap]::ArrowAnchor
  $g.DrawLine($pen,[single]$x,[single]$y,[single]($x+$dir*24),[single]$y)
  $label=if($u.side -eq 'BLUE'){'B'+($u.id -split '-')[1]}else{'R'+($u.id -split '-')[1]}
  $g.DrawString($label,$fText,$brush,[single]($x-17),[single]($y-32))
  $pen.Dispose()
 }
 $g.DrawString('X: 중심선 x=2000m 기준 거리 (m)',$fSmall,$dark,$left+98,400)
 $dist=($d.paired_distances | ForEach-Object { [math]::Round($_) }) -join ' / '
 $g.DrawString("같은 번호 B↔R: $dist m",$fText,$dark,$left+25,447)
 $adj=[math]::Round($d.own_adjacent_distance,1)
 $g.DrawString("인접 아군 중심 간격: $adj m",$fText,$dark,$left+25,481)
}
$g.DrawString('Y축도 y=2000m 기준 상대 거리. 세 그림의 축척은 동일하며 모든 배치의 최근접 적 중심 거리는 150m입니다.',$fText,$dark,25,536)
$g.DrawString('부대의 실제 점유 영역·가림·사거리 원은 생략했습니다. 화살표는 초기 관측/지향 방향입니다.',$fText,$dark,25,574)
$path=Join-Path $PSScriptRoot 'layouts.png'
$bmp.Save($path,[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose();$bmp.Dispose()
