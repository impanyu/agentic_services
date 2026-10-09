"""Photography intent mapped to broad candidate categories, not aesthetic proof."""
PHOTO_STYLES = {
    'nature': {'label':'Nature & calm','description':'Greenery, soft scenery and relaxed outdoor portraits.','categories':['park','nature','viewpoint']},
    'urban': {'label':'Urban & architectural','description':'Bold structures, city lines and striking perspectives.','categories':['historic','attraction','viewpoint']},
    'vintage': {'label':'Vintage & nostalgic','description':'Old-world textures, historic details and timeless backdrops.','categories':['historic','museum','attraction']},
    'iconic': {'label':'Iconic & cinematic','description':'Recognizable landmarks and dramatic wide compositions.','categories':['attraction','viewpoint','historic']},
    'artistic': {'label':'Artsy & colorful','description':'Public art, playful color and creative visual details.','categories':['artwork','museum','historic']},
    'waterside': {'label':'Water & reflections','description':'Riverbanks, shorelines and compositions with visible water.','categories':['nature','viewpoint','park']},
    'minimal': {'label':'Clean & minimal','description':'Simple geometry, uncluttered backgrounds and negative space.','categories':['artwork','museum','historic','park']},
    'adventure': {'label':'Wild & adventurous','description':'Rugged landscapes and expansive outdoor scenes.','categories':['nature','viewpoint','recreation']},
}


def mapped_categories(styles):
    return sorted({category for style in styles for category in PHOTO_STYLES[style]['categories']})


def style_briefs(styles):
    return [{'id':style,'label':PHOTO_STYLES[style]['label'],'description':PHOTO_STYLES[style]['description']} for style in styles or []]


# Discovery hints, not claims that a place visually matches the mood.
MOOD_PLACE_QUERIES = {
    'nature': ['parks', 'botanical gardens'],
    'urban': ['architecture landmarks', 'city plazas'],
    'vintage': ['historic districts', 'historic buildings'],
    'iconic': ['famous landmarks', 'scenic viewpoints'],
    'artistic': ['public art', 'street murals'],
    'waterside': ['waterfront promenades', 'lakeside parks'],
    'minimal': ['modern architecture', 'public plazas'],
    'adventure': ['hiking trails', 'scenic viewpoints'],
}

def discovery_queries(poi_queries, styles, categories=None):
    # Explicit user targets are authoritative; mood still guides image scoring.
    if poi_queries:
        return list(dict.fromkeys(poi_queries))[:4]
    if categories:
        return list(dict.fromkeys(c.replace('_',' ') for c in categories))[:4]
    from itertools import zip_longest
    batches=zip_longest(*(MOOD_PLACE_QUERIES[s] for s in styles or []))
    terms=list(dict.fromkeys(q for batch in batches for q in batch if q))
    return terms[:4] or ['scenic places and tourist attractions']
