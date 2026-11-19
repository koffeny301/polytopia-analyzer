"""Тесты генерации HTML-отчёта."""

from polytopia_analyzer.models import Match, MatchMeta, Player, TurnRecord
from polytopia_analyzer.report import build_report, load_bundled_plotly

CHART_IDS = [
    "chart-score", "chart-income", "chart-army", "chart-cities",
    "chart-kills", "chart-losses", "chart-damage", "chart-combat", "chart-ranks",
]


def make_match() -> Match:
    meta = MatchMeta(
        title="Великая битва",
        mode="Domination",
        date="2026-11-12",
        link="https://share.polytopia.io/g/a4d531fd-1b81-428c-d81f-08dae2baaab2",
        turn_limit=30,
    )
    players = [Player(name="A", tribe="Bardur"), Player(name="B", tribe="Quetzali")]
    records = [
        TurnRecord(turn=1, player="A", score=100, income=2, army=1, cities=1),
        TurnRecord(turn=1, player="B", score=120, income=3, army=2, cities=1),
        TurnRecord(turn=2, player="A", score=250, income=4, army=3, cities=1, kills=2, damage=10),
        TurnRecord(turn=2, player="B", score=180, income=3, army=4, cities=1, losses=2),
        TurnRecord(turn=3, player="A", score=400, income=5, army=2, cities=2, kills=1, losses=1, captured=1, damage=4),
        TurnRecord(turn=3, player="B", score=350, income=6, army=3, cities=1, lost_cities=1),
    ]
    return Match(meta=meta, players=players, records=records)


def test_report_is_full_html_document_with_charts():
    html = build_report(make_match())
    assert "<!DOCTYPE html>" in html
    assert "Plotly.newPlot" in html
    for cid in CHART_IDS:
        assert f'id="{cid}"' in html
    assert "Урон по ходам" in html and "Нанесено урона" in html


def test_damage_chart_absent_without_damage():
    """Старые матчи без урона: ни графика урона, ни нулевого бара в итогах."""
    m = make_match()
    for r in m.records:
        r.damage = 0.0
    html = build_report(m)
    assert 'id="chart-damage"' not in html
    assert "Урон по ходам" not in html
    assert "Нанесено урона" not in html


def test_report_contains_title_meta_and_players():
    html = build_report(make_match())
    assert "Великая битва" in html
    assert "Domination" in html
    assert "share.polytopia.io/g/a4d531fd" in html
    assert "Bardur" in html


def test_report_winner_banner_and_standings_order():
    html = build_report(make_match())
    banner = html[html.index("winner-banner panel"):html.index("winner-banner panel") + 400]
    assert "A" in banner
    assert html.index('data-player="A"') < html.index('data-player="B"')


def test_report_embeds_passed_plotly_js():
    html = build_report(make_match(), plotly_js="/*TEST_PLOTLY_JS*/")
    assert "/*TEST_PLOTLY_JS*/" in html
    assert "cdn.plot.ly" not in html


def test_report_cdn_when_plotly_js_none():
    html = build_report(make_match(), plotly_js=None)
    assert "cdn.plot.ly" in html


def test_bundled_plotly_asset_exists():
    js = load_bundled_plotly()
    assert js is not None and len(js) > 100_000


def test_report_escapes_player_html():
    m = make_match()
    m.players[0].name = "<img src=x onerror=alert(1)>"
    for r in m.records:
        if r.player == "A":
            r.player = "<img src=x onerror=alert(1)>"
    html = build_report(m)
    assert "<img src=x onerror" not in html
    assert "&lt;img" in html


def test_report_shows_source_chip():
    m = make_match()
    m.meta.source = "shim"
    html = build_report(m)
    assert "Источник" in html
    assert "состояние игры (шим)" in html


def test_report_handles_match_without_records():
    m = Match(meta=MatchMeta(title="Пусто"), players=[Player(name="A")], records=[])
    html = build_report(m)
    assert "<!DOCTYPE html>" in html
