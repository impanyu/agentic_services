"""Package the active iOS artwork and a content version for WebKit update checks."""
from pathlib import Path
import hashlib
import json
import re
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
SITE=ROOT/'photo-scout-site'
ICONS=ROOT/'ios/PhotoScout/Resources/Assets.xcassets/AppIcon.appiconset'
name=json.loads((ICONS/'Contents.json').read_text())['images'][0]['filename']
with Image.open(ICONS/name) as icon:
    for size in (64,180,192,512):
        icon.resize((size,size),Image.Resampling.LANCZOS).save(SITE/f'app-icon-{size}.png',optimize=True)
files=sorted(p for p in SITE.iterdir() if p.suffix in ('.js','.css','.html','.webmanifest','.png') and p.name!='version.json')
hash_value=hashlib.sha256()
for path in files:
    hash_value.update(path.name.encode());data=path.read_bytes()
    if path.name=="index.html":data=re.sub(rb'(name="photo-scout-version" content=")[a-f0-9]{16}',rb'\g<1>0000000000000000',data)
    hash_value.update(data)
version=hash_value.hexdigest()[:16]
index=SITE/'index.html'
index.write_text(re.sub(r'(name="photo-scout-version" content=")[a-f0-9]{16}',lambda m:m[1]+version,index.read_text()))
(SITE/'version.json').write_text(json.dumps({'version':version})+'\n')
print('Photo Scout web assets',version)
