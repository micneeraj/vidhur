"""Entity resolution: banded name similarity (spec: AGENTS.md section 7.1, merge detection).

  similarity >= 0.85          -> auto-merge   (0.85 EXACTLY auto-merges)
  0.82 <= similarity < 0.85   -> operator review queue (0.82 EXACTLY queues)
  similarity < 0.82           -> new entity
"""

from .stable_ids import normalize_name


def levenshtein(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        curr = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[len(b)]


def name_similarity(raw_a, raw_b):
    a, b = normalize_name(raw_a), normalize_name(raw_b)
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1
    return 1 - levenshtein(a, b) / max_len


def decide(similarity, auto_merge_at=0.85, review_lower=0.82):
    if similarity >= auto_merge_at:
        return "auto-merge"
    if similarity >= review_lower:
        return "review"
    return "new"


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0
    return len(sa & sb) / (len(sa) + len(sb) - len(sa & sb))
