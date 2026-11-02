from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_every_id_used_by_the_new_user_blocks_exists_in_the_page():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    for ident in ("picks", "firstWeek", "listingScore", "tilesMore", "cabPerson", "cabCompany", "cabDating", "cabPersonSub", "cabCompanySub", "postAsBox"):
        assert f'id="{ident}"' in html, ident
        assert f'"#{ident}"' in js or ident in ("cabPerson", "cabCompany"), ident
    assert 'id="postAs"' not in html and "fillPostAs" not in js        # no «post as» chooser: the place you come from decides


def test_dark_theme_is_opt_in_and_never_touches_the_light_theme():
    css = (STATIC / "app.css").read_text()
    block = css[css.index("/* тёмная тема"):]
    for line in block.split("\n"):
        if "{" in line and not line.startswith(("/*", "html[data-theme=dark]")):
            raise AssertionError("a rule of the dark block is not scoped to the dark theme: " + line[:80])
    js = (STATIC / "app.js").read_text()
    assert 'localStorage.getItem(THEME_KEY) || "light"' in js        # the default is the light theme as before


def test_translation_tables_cover_every_static_text_and_json_is_fresh():
    import importlib.util
    spec = importlib.util.spec_from_file_location("i18n_build", STATIC.parent.parent / "tools" / "i18n_build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.missing() == [], "new Russian text in index.html without a translation: run tools/i18n_build.py --missing"
    import json
    en = json.loads((STATIC / "i18n" / "en.json").read_text(encoding="utf-8"))
    ko = json.loads((STATIC / "i18n" / "ko.json").read_text(encoding="utf-8"))
    assert en == {r: e for r, e, _ in mod.T} and ko == {r: k for r, _, k in mod.T}, "run python3 tools/i18n_build.py and commit the json"
    assert all(en.values()) and all(ko.values())
