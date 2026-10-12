"""Select a bounded, target-facing review of fully rejected panoramas."""
import math

from .sources import distance


def review_candidates(rows, assessments, limit=50):
    checks={a.image_id:a for a in assessments};groups={}
    for row in rows:
        reference=row.get('imageUrl','')
        if row.get('provider')=='google-street-view' and reference.startswith('google-streetview://'):
            groups.setdefault(reference.split('/')[2],[]).append(row)
    selected=[]
    for views in groups.values():
        if len({r.get('viewHeadingDegrees') for r in views})<8:continue
        if any(r['id'] not in checks or checks[r['id']].matches_request for r in views):continue
        choices=[]
        for row in views:
            for poi in row.get('poiCandidates', [row['poi']] if row.get('poi') else []):
                if not all(isinstance(obj.get(k),(float,int)) and math.isfinite(obj[k]) for obj in (row,poi) for k in ('lat','lon')):continue
                meters=distance((row['lat'],row['lon']),(poi['lat'],poi['lon']))
                if meters>200:continue
                lat1,lat2=math.radians(row['lat']),math.radians(poi['lat'])
                delta=math.radians(poi['lon']-row['lon'])
                bearing=math.degrees(math.atan2(math.sin(delta)*math.cos(lat2),math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(delta)))%360
                offset=abs((row['viewHeadingDegrees']-bearing+180)%360-180)
                choices.append((meters,offset,row['id'],row))
        if choices:selected.append(min(choices,key=lambda c:c[:3])[-1])
        if len(selected)>=limit:break
    return selected
