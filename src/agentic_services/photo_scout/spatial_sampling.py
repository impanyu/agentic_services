"""Bounded spatial ordering keeps dense feature candidates across the search area."""
import math
from collections import deque


def spread_points(rows, lat, lon, radius, *, cells=8):
    """Balance comparable soft priorities with coverage, without excluding any row."""
    if not rows or radius <= 0:
        return list(rows)
    dy = radius / 111320
    dx = dy / max(.01, math.cos(math.radians(lat)))
    buckets = {}
    for row in rows:
        x = min(cells-1, max(0, int((row['lon']-lon+dx)/(2*dx)*cells)))
        y = min(cells-1, max(0, int((row['lat']-lat+dy)/(2*dy)*cells)))
        buckets.setdefault((x,y), []).append(row)
    queues = []
    for key, values in buckets.items():
        values.sort(key=lambda p: (p.get('selectionPriority',[]),bool(p.get('unknownAttributes')),p.get('distanceMeters', 0)))
        queues.append((key, deque(values)))
    queues.sort(key=lambda pair: pair[1][0].get('distanceMeters', 0))
    result = []
    while queues:
        remaining = []
        for key, queue in queues:
            result.append(queue.popleft())
            if queue:
                remaining.append((key,queue))
        queues = remaining
    hints={tuple(row.get('selectionHints',[])) for row in rows}
    if len(hints)!=1 or not next(iter(hints)):
        return result
    # A purely spatial round-robin can spend the entire imagery budget on
    # weak matches in sparse cells. Reserve two of every three positions for
    # the strongest comparable discovery proxies; keep one for area coverage.
    # Unlike a threshold, this preserves unknown/weak candidates and every ID.
    priority=deque(sorted(rows,key=lambda p:(p.get('selectionPriority',[]),p.get('distanceMeters',0))))
    spatial=deque(result);ordered=[];seen=set()
    while priority or spatial:
        queue=spatial if len(ordered)%3==2 else priority
        while queue and queue[0]['id'] in seen:queue.popleft()
        if not queue:
            queue=priority if queue is spatial else spatial
            while queue and queue[0]['id'] in seen:queue.popleft()
        if not queue:break
        row=queue.popleft();seen.add(row['id']);ordered.append(row)
    return ordered
