"""Persistent model assessments only; never stores image bytes or raw preferences."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time


class ScoreCache:
    def __init__(self, path):
        self.path = path
        self.ttl = max(0, int(os.getenv('PHOTO_SCOUT_SCORE_CACHE_DAYS', '30'))) * 86400
        self.enabled = self.ttl > 0
        if not self.enabled:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=15) as db:
                db.execute('CREATE TABLE IF NOT EXISTS photo_scout_score_cache (cache_key TEXT PRIMARY KEY, assessment TEXT NOT NULL, expires REAL NOT NULL)')
                db.execute('DELETE FROM photo_scout_score_cache WHERE expires<=?', (time.time(),))
        except (sqlite3.Error, OSError) as error:
            self.disable(error)

    def disable(self, error):
        self.enabled = False
        logging.getLogger(__name__).warning('Photo score cache unavailable: %s', type(error).__name__)

    @staticmethod
    def key(row, payload, model, instructions):
        image = {k: v for k, v in row.items() if k not in ('distanceMeters', 'poiDistanceMeters', 'author', 'explorationReason')}
        if image.get('poiCandidates'):
            image['poiCandidates'] = sorted(image['poiCandidates'], key=lambda p: p['id'])
        from .streetview_tiles import profile
        data = {'version': 2, 'subjectRole':getattr(payload,'subjectRole','scene'), 'requirements':[r.model_dump() for r in getattr(payload,'requirements',[])], 'image': image, 'model': model, 'instructions': instructions,
                'photoStyles': sorted(payload.photoStyles or []), 'preferences': payload.preferences.strip(),
                'geographicKinds': sorted(getattr(payload,'geographicKinds',[])),
                'geographicCombination':getattr(payload,'geographicCombination','all'),'featureCombination':getattr(payload,'featureCombination','all'),
                'searchProgram':payload.searchProgram.retrieval().model_dump() if getattr(payload,'searchProgram',None) else None,'searchBranches':[b.model_dump() for b in getattr(payload,'searchBranches',[])],'osmFeatures': [q.model_dump() for q in getattr(payload,'osmFeatures',[])],
                'poiQueries': sorted(payload.poiQueries), 'scoringIntent': payload.scoringIntent.strip()}
        if row.get('provider')=='google-street-view':
            data['imageryProfile']=profile()
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def get(self, key):
        return self.get_many([key]).get(key)

    def get_many(self, keys):
        """Read one request's assessments together instead of opening per view."""
        if not self.enabled:
            return {}
        try:
            found={};keys=list(dict.fromkeys(keys));now=time.time()
            with sqlite3.connect(self.path, timeout=15) as db:
                for offset in range(0,len(keys),500):
                    chunk=keys[offset:offset+500]
                    rows=db.execute('SELECT cache_key,assessment FROM photo_scout_score_cache WHERE expires>? AND cache_key IN ('+','.join('?' for _ in chunk)+')',(now,*chunk)).fetchall()
                    for key,value in rows:
                        try:found[key]=json.loads(value)
                        except ValueError:continue
            return found
        except (sqlite3.Error, ValueError) as error:
            self.disable(error)
            return {}

    def put(self, entries):
        if not self.enabled or not entries:
            return
        try:
            with sqlite3.connect(self.path, timeout=15) as db:
                db.executemany('INSERT OR REPLACE INTO photo_scout_score_cache VALUES(?,?,?)',
                    [(key, json.dumps(assessment), time.time()+self.ttl) for key, assessment in entries])
        except (sqlite3.Error, OSError) as error:
            self.disable(error)
