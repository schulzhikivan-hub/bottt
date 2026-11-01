"""One place for the rules that decide in which order paid things are shown and where ads may stand.

Used by the lists of listings and by the dating feed, so that VIP, pinning, bumping, promotions and ads can never
disagree with each other.

Order of priority of the lanes (from the top of the screen):
  1. pinned items (at most MAX_PINNED, a separate lane over the list);
  2. the list itself, sorted by relevance or date, where VIP and bumped items get a lift but obey the window rule;
  3. ads: a separate lane. An ad never takes one of the first AD_AFTER places and never counts in «2 из 5».
A VIP that was given free by a promotion is shown with a mark but gets no lift (kind «promo»)."""
import math

WINDOW = 5                 # look at any five places in a row ...
MAX_PAID_IN_WINDOW = 2     # ... at most two of them may be paid (VIP or bumped)
PAGE_CAP = 0.30            # paid items are at most 30% of a page (rounded up, at least one)
MAX_PINNED = 2             # pinned lane over the list
AD_AFTER = 3               # an ad stands after the first three organic items
AD_EVERY = 6               # at most one ad per screen (six cards)
AD_MAX_ON_PAGE = 3
AD_MAX_HOME = 2

PAID_KINDS = {"vip", "bump"}    # kinds that get a lift
LANE_PINNED = "pin"


def arrange(items: list, kind_of) -> list:
    """Return the same items in the final order. `kind_of(item)` gives 'pin', 'vip', 'bump', 'promo' or ''.
    The relative order of the input (by relevance or date) is kept as far as the rules allow."""
    pinned = [x for x in items if kind_of(x) == LANE_PINNED][:MAX_PINNED]
    pinned_ids = {id(x) for x in pinned}
    rest = [x for x in items if id(x) not in pinned_ids]          # extra pinned items are treated like the rest
    paid_total = sum(1 for x in rest if kind_of(x) in PAID_KINDS)
    cap = max(1, math.ceil(PAGE_CAP * len(items))) if paid_total else 0

    out: list = []
    deferred: list = []        # paid items waiting for a free place
    used_paid = 0

    def paid_in_last(n: int) -> int:
        return sum(1 for y in out[-n:] if kind_of(y) in PAID_KINDS)

    def release():
        nonlocal used_paid
        while deferred and used_paid < cap and paid_in_last(WINDOW - 1) < MAX_PAID_IN_WINDOW:
            out.append(deferred.pop(0))
            used_paid += 1

    for x in rest:
        if kind_of(x) in PAID_KINDS:
            if used_paid < cap and paid_in_last(WINDOW - 1) < MAX_PAID_IN_WINDOW and not deferred:
                out.append(x)
                used_paid += 1
            else:
                deferred.append(x)
        else:
            out.append(x)
            release()
    out.extend(deferred)       # nothing else is left to stand between them
    return pinned + out


def window_ok(kinds: list[str]) -> bool:
    """True if in no five places in a row there are more than two paid ones (pinned lane is not counted)."""
    body = [k for k in kinds if k != LANE_PINNED]
    for i in range(len(body)):
        if sum(1 for k in body[i:i + WINDOW] if k in PAID_KINDS) > MAX_PAID_IN_WINDOW:
            return False
    return True


def ad_slots(organic_count: int, page: int, where: str = "list", all_pages: bool = False) -> list[int]:
    """Indexes (0-based, in the list of organic items) before which an ad may be inserted.
    First page only (unless switched on), never before the third organic item, one per screen, home up to two."""
    if organic_count < AD_AFTER or (page > 0 and not all_pages):
        return []
    limit = AD_MAX_HOME if where == "home" else AD_MAX_ON_PAGE
    slots = []
    pos = AD_AFTER
    while pos <= organic_count and len(slots) < limit:
        slots.append(pos)
        pos += AD_EVERY
    return slots
