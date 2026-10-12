"""Bounded spatial ordering keeps dense feature candidates across the search area."""
import math
from collections import deque


def spread_points(rows, lat, lon, radius, *, cells=8):
    """Round-robin occupied cells; retain every ID and prefer known attributes locally."""
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
        values.sort(key=lambda p: (bool(p.get('unknownAttributes')), p.get('distanceMeters', 0)))
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
    return result
