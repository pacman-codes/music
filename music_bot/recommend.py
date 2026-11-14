"""Local radio from saved files; future discovery providers must supply candidates."""
import random
from collections import Counter

def wave(tracks, liked_ids, disliked_ids=frozenset(), count=10, seed=None):
    pool = list({t.id: t for t in tracks if t.id not in disliked_ids}.values())
    if count <= 0:
        return []
    rng = random.Random(seed)
    fav_artist = Counter(t.artist.casefold() for t in pool if t.id in liked_ids)
    seen_artist = Counter()
    out = []
    while pool and len(out) < count:
        weights = []
        for t in pool:
            artist = t.artist.casefold()
            score = 1 + (3 if t.id in liked_ids else 0)
            if t.id not in liked_ids and artist in fav_artist:
                score += 1.5
            score /= 1 + 2 * seen_artist[artist]
            weights.append(score)
        index = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        t = pool.pop(index)
        seen_artist[t.artist.casefold()] += 1
        out.append(t)
    return out
