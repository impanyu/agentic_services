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
        data = {'version': 2, 'requirements':[r.model_dump() for r in getattr(payload,'requirements',[])], 'image': image, 'model': model, 'instructions': instructions,
                'photoStyles': sorted(payload.photoStyles or []), 'preferences': payload.preferences.strip(),
                'geographicKinds': sorted(getattr(payload,'geographicKinds',[])),
                'geographicCombination':getattr(payload,'geographicCombination','all'),'featureCombination':getattr(payload,'featureCombination','all'),
                'searchProgram':payload.searchProgram.retrieval().model_dump() if getattr(payload,'searchProgram',None) else None,'searchBranches':[b.model_dump() for b in getattr(payload,'searchBranches',[])],'osmFeatures': [q.model_dump() for q in getattr(payload,'osmFeatures',[])],
                'poiQueries': sorted(payload.poiQueries), 'scoringIntent': payload.scoringIntent.strip()}
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def get(self, key):
        if not self.enabled:
            return None
        try:
            with sqlite3.connect(self.path, timeout=15) as db:
                row = db.execute('SELECT assessment FROM photo_scout_score_cache WHERE cache_key=? AND expires>?', (key, time.time())).fetchone()
            return json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError) as error:
            self.disable(error)
            return None

    def put(self, entries):
        if not self.enabled or not entries:
            return
        try:
            with sqlite3.connect(self.path, timeout=15) as db:
                db.executemany('INSERT OR REPLACE INTO photo_scout_score_cache VALUES(?,?,?)',
                    [(key, json.dumps(assessment), time.time()+self.ttl) for key, assessment in entries])
        except (sqlite3.Error, OSError) as error:
            self.disable(error)
