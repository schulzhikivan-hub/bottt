from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_every_id_used_by_the_new_user_blocks_exists_in_the_page():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    for ident in ("picks", "firstWeek", "listingScore", "tilesMore", "cabPerson", "cabCompany", "cabDating", "cabPersonSub", "cabCompanySub", "postAsBox"):
        assert f'id="{ident}"' in html, ident
        assert f'"#{ident}"' in js or ident in ("cabPerson", "cabCompany"), ident
    assert 'id="postAs"' not in html and "fillPostAs" not in js        # no «post as» chooser: the place you come from decides
