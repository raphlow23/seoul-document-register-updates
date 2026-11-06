"""Package the approved transparent artwork at full optical size in every ICO frame."""
from pathlib import Path
import struct
import sys
from io import BytesIO
from PIL import Image,ImageDraw

base=Path(__file__).resolve().parent.parent
source=Image.open(sys.argv[1]).convert('RGBA')
# Ignore near-transparent export specks when calculating the artwork bounds.
bounds=source.getchannel('A').point(lambda a:255 if a>=8 else 0).getbbox()
assert bounds
art=source.crop(bounds)
sizes=(16,20,24,32,40,48,64,96,128,256)
old=Image.open(base/'assets/app_icon.ico')
frames=[];payloads=[];report=[]
for size in sizes:
    padding=0 if size<=64 else 1
    scale=(size-2*padding)/max(art.size)
    glyph=art.resize((round(art.width*scale),round(art.height*scale)),Image.Resampling.LANCZOS)
    frame=Image.new('RGBA',(size,size));frame.alpha_composite(glyph,((size-glyph.width)//2,(size-glyph.height)//2))
    # Each native Windows size gets its own full-size frame, rather than a scaled padded canvas.
    actual=frame.getchannel('A').point(lambda a:255 if a>=128 else 0).getbbox()
    assert actual and actual[2]-actual[0]>=size-2*padding-1,(size,actual)
    frames.append(frame);stream=BytesIO();frame.save(stream,format='PNG');payloads.append(stream.getvalue())
    report.append((size,actual))
offset=6+16*len(sizes);ico=bytearray(struct.pack('<HHH',0,1,len(sizes)))
for size,data in zip(sizes,payloads):
    ico.extend(struct.pack('<BBBBHHII',size if size<256 else 0,size if size<256 else 0,0,0,1,32,len(data),offset));offset+=len(data)
for data in payloads:ico.extend(data)
preview=Image.new('RGB',(900,260),'#eef0f5');draw=ImageDraw.Draw(preview)
for i,size in enumerate((16,24,32,48)):
    for row,image in enumerate((old.ico.getimage((size,size)).convert('RGBA'),frames[sizes.index(size)])):
        image=image.resize((86,86),Image.Resampling.NEAREST);preview.paste(image,(135+i*180,12+row*122),image)
    draw.text((135+i*180,105),str(size)+' px',fill='black')
draw.text((10,45),'Before',fill='black');draw.text((10,175),'After',fill='black')
(base/'tmp').mkdir(exist_ok=True);preview.save(base/'tmp/icon-size-comparison.png')
(base/'assets/app_icon.ico').write_bytes(ico);frames[-1].save(base/'assets/app_icon.png')
check=Image.open(base/'assets/app_icon.ico');assert check.ico.sizes()=={(n,n) for n in sizes}
print('Native frame visible bounds:',report)
