"""Генерация интерактивного HTML-отчёта в стиле Civilization (plotly.js)."""

from __future__ import annotations

import datetime as _dt
import html as _html
import json
from pathlib import Path
from typing import Optional

from . import __version__
from .analysis import (
    awards,
    cumulative_series,
    leader_changes,
    metric_series,
    player_stats,
    rank_history,
    score_series,
    standings,
)
from .models import Match

_BUNDLED_PLOTLY = Path(__file__).parent / "assets" / "plotly-basic.min.js"
_PLOTLY_CDN = "https://cdn.plot.ly/plotly-basic-2.35.2.min.js"
_AUTO = object()

COMBAT_BAR_COLORS = {
    "kills": "#e15759",
    "losses": "#ff9d9a",
    "captured": "#4e79a7",
    "lost_cities": "#a0cbe8",
}
COMBAT_BAR_LABELS = {
    "kills": "Убито юнитов врага",
    "losses": "Потеряно юнитов",
    "captured": "Захвачено городов",
    "lost_cities": "Потеряно городов",
}


def load_bundled_plotly() -> Optional[str]:
    """Возвращает текст встроенного plotly-basic.min.js или None."""
    if _BUNDLED_PLOTLY.exists():
        return _BUNDLED_PLOTLY.read_text(encoding="utf-8")
    return None


def esc(value) -> str:
    return _html.escape(str(value), quote=True)


# ---------- plotly-фигуры ----------

def _base_layout(title: str, y_title: str = "") -> dict:
    return {
        "title": {"text": title, "font": {"size": 17, "color": "#f0e6d2"}},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(16,18,32,0.85)",
        "font": {"family": "Segoe UI, Roboto, sans-serif", "color": "#cfd2e3", "size": 12},
        "margin": {"l": 60, "r": 20, "t": 48, "b": 40},
        "xaxis": {"title": {"text": "Ход"}, "gridcolor": "rgba(255,255,255,0.08)",
                   "zeroline": False, "dtick": 1},
        "yaxis": {"title": {"text": y_title}, "gridcolor": "rgba(255,255,255,0.08)",
                   "zeroline": False},
        "legend": {"orientation": "h", "y": -0.2, "x": 0},
        "hovermode": "x unified",
    }


def _line_traces(series: dict[str, list[tuple[int, float]]], colors: dict[str, str]) -> list[dict]:
    traces = []
    for name, points in series.items():
        traces.append({
            "type": "scatter",
            "mode": "lines+markers",
            "name": name,
            "x": [p[0] for p in points],
            "y": [p[1] for p in points],
            "line": {"color": colors.get(name), "width": 3},
            "marker": {"size": 5},
        })
    return traces


def _build_figures(match: Match) -> dict[str, dict]:
    colors = {n: s.color for n, s in player_stats(match).items()}
    figs: dict[str, dict] = {}

    figs["chart-score"] = {
        "data": _line_traces(score_series(match), colors),
        "layout": _base_layout("Общая стоимость (счёт) по ходам", "Очки"),
    }
    figs["chart-income"] = {
        "data": _line_traces(metric_series(match, "income"), colors),
        "layout": _base_layout("Доход по ходам", "☆ за ход"),
    }
    army_layout = _base_layout("Размер армии по ходам", "Юниты")
    figs["chart-army"] = {
        "data": _line_traces(metric_series(match, "army"), colors),
        "layout": army_layout,
    }
    figs["chart-cities"] = {
        "data": _line_traces(metric_series(match, "cities"), colors),
        "layout": _base_layout("Число городов по ходам", "Города"),
    }
    figs["chart-kills"] = {
        "data": _line_traces(cumulative_series(match, "kills"), colors),
        "layout": _base_layout("Накопительно: убито юнитов врага", "Юниты"),
    }
    figs["chart-losses"] = {
        "data": _line_traces(cumulative_series(match, "losses"), colors),
        "layout": _base_layout("Накопительно: потеряно своих юнитов", "Юниты"),
    }

    stats = standings(match)
    combat = {"data": [], "layout": _base_layout("Боевые итоги матча", "Количество")}
    combat["layout"]["legend"] = {"orientation": "h", "y": -0.25, "x": 0}
    for field_name, label in COMBAT_BAR_LABELS.items():
        combat["data"].append({
            "type": "bar",
            "name": label,
            "x": [s.name for s in stats],
            "y": [getattr(s, f"total_{field_name}") if field_name != "kills" else s.total_kills
                  for s in stats],
            "marker": {"color": COMBAT_BAR_COLORS[field_name]},
        })
    figs["chart-combat"] = combat

    rh = rank_history(match)
    ranks = {"data": [], "layout": _base_layout("Смена мест по ходам", "Место")}
    ranks["layout"]["yaxis"].update({"autorange": "reversed", "dtick": 1, "tick0": 1})
    for name, points in rh.items():
        ranks["data"].append({
            "type": "scatter",
            "mode": "lines+markers",
            "name": name,
            "x": [p[0] for p in points],
            "y": [p[1] for p in points],
            "line": {"color": colors.get(name), "width": 3, "shape": "hv"},
            "marker": {"size": 6},
        })
    figs["chart-ranks"] = ranks
    return figs


# ---------- HTML-блоки ----------

_CSS = """
:root { --bg:#14162a; --panel:#1e2140; --panel2:#262a52; --gold:#d4af37;
        --text:#e8e6df; --muted:#9a9db5; --border:#34396b; }
* { box-sizing: border-box; }
body { margin:0; background:radial-gradient(1200px 600px at 50% -100px, #232752 0%, var(--bg) 60%);
       color:var(--text); font-family:'Segoe UI', Roboto, Arial, sans-serif; }
.wrap { max-width:1100px; margin:0 auto; padding:28px 20px 60px; }
h1 { font-family:Georgia,'Times New Roman',serif; color:var(--gold); letter-spacing:.5px;
     margin:0 0 6px; font-size:34px; text-align:center; }
h2 { font-family:Georgia,serif; color:var(--gold); font-size:22px; margin:34px 0 14px;
     border-bottom:1px solid var(--border); padding-bottom:8px; }
.subtitle { text-align:center; color:var(--muted); margin-bottom:18px; }
.chips { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-bottom:20px; }
.chip { background:var(--panel); border:1px solid var(--border); border-radius:20px;
        padding:5px 14px; font-size:13px; color:var(--text); }
.chip b { color:var(--gold); font-weight:600; }
.chip a { color:#8fd3ff; text-decoration:none; }
.winner-banner { background:linear-gradient(135deg,#2a2f5e,#1e2140); border:1px solid var(--gold);
        border-radius:14px; padding:20px 26px; text-align:center; margin:8px 0 6px; }
.winner-banner .crown { font-size:34px; }
.winner-banner .name { font-family:Georgia,serif; font-size:26px; color:var(--gold); margin:4px 0; }
.winner-banner .details { color:var(--muted); font-size:14px; }
.panel { background:var(--panel); border:1px solid var(--border); border-radius:12px;
         padding:14px; margin-bottom:8px; }
table.standings { width:100%; border-collapse:collapse; font-size:14px; }
table.standings th { color:var(--gold); text-align:left; padding:9px 10px;
        border-bottom:2px solid var(--border); font-weight:600; white-space:nowrap; }
table.standings td { padding:8px 10px; border-bottom:1px solid rgba(255,255,255,.06);
        white-space:nowrap; }
table.standings tr:hover td { background:rgba(255,255,255,.03); }
.rank { font-family:Georgia,serif; font-size:17px; color:var(--gold); width:34px; }
.dot { display:inline-block; width:11px; height:11px; border-radius:50%; margin-right:8px; }
.pname { font-weight:600; }
.tribe { color:var(--muted); font-size:12px; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
@media (max-width:860px){ .grid2 { grid-template-columns:1fr; } }
.awards { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
.award { background:var(--panel2); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }
.award .title { font-size:15px; color:var(--gold); font-weight:600; }
.award .who { font-size:17px; margin:5px 0 2px; font-weight:600; }
.award .why { color:var(--muted); font-size:13px; }
.timeline { display:flex; flex-wrap:wrap; gap:8px; }
.timeline .step { background:var(--panel2); border:1px solid var(--border);
        border-radius:10px; padding:7px 12px; font-size:13px; }
.timeline .step b { color:var(--gold); }
footer { margin-top:44px; text-align:center; color:var(--muted); font-size:12px; }
"""


def _standings_table(match: Match) -> str:
    stats = standings(match)
    rows = []
    for i, s in enumerate(stats, start=1):
        kd = f"{s.kd:.2f}" if s.total_losses or s.total_kills else "—"
        rows.append(
            f'<tr data-player="{esc(s.name)}">'
            f'<td class="rank">{i}</td>'
            f'<td><span class="dot" style="background:{esc(s.color)}"></span>'
            f'<span class="pname">{esc(s.name)}</span><br>'
            f'<span class="tribe">{esc(s.tribe) if s.tribe else "&nbsp;"}</span></td>'
            f"<td><b>{s.final_score:g}</b></td>"
            f"<td>+{s.score_growth:g}</td>"
            f"<td>{s.avg_income:.1f}</td>"
            f"<td>{s.peak_army}</td>"
            f"<td>{s.max_cities}</td>"
            f"<td>{s.total_kills}</td>"
            f"<td>{s.total_losses}</td>"
            f"<td>{kd}</td>"
            f"<td>{s.total_captured}</td>"
            f"<td>{s.total_lost_cities}</td>"
            f"<td>{s.total_damage:g}</td>"
            f"</tr>"
        )
    header = ("<tr><th>#</th><th>Игрок</th><th>Счёт</th><th>Прирост</th><th>Доход ☆/ход</th>"
              "<th>Пик армии</th><th>Макс. городов</th><th>Убито</th><th>Потеряно</th>"
              "<th>K/D</th><th>Захвачено</th><th>Потеряно городов</th><th>Урон</th></tr>")
    return f'<table class="standings">{header}{"".join(rows)}</table>'


def _winner_banner(match: Match) -> str:
    stats = standings(match)
    if not stats:
        return ""
    w = stats[0]
    margin = ""
    if len(stats) > 1:
        gap = w.final_score - stats[1].final_score
        margin = f"отрыв от второго места: +{gap:g} очков"
    tribe = f" ({esc(w.tribe)})" if w.tribe else ""
    return (
        '<div class="winner-banner panel"><div class="crown">🏆</div>'
        f'<div class="name">Победа: {esc(w.name)}{tribe}</div>'
        f'<div class="details">итоговый счёт <b>{w.final_score:g}</b>'
        f'{(" · " + margin) if margin else ""}</div></div>'
    )


def _meta_chips(match: Match) -> str:
    m = match.meta
    chips = []
    if m.mode:
        chips.append(f'<span class="chip">Режим: <b>{esc(m.mode)}</b></span>')
    if m.map:
        chips.append(f'<span class="chip">Карта: <b>{esc(m.map)}</b></span>')
    if m.date:
        chips.append(f'<span class="chip">Дата: <b>{esc(m.date)}</b></span>')
    if m.turn_limit:
        chips.append(f'<span class="chip">Лимит: <b>{m.turn_limit}</b> ходов</span>')
    chips.append(f'<span class="chip">Записано ходов: <b>{match.max_turn()}</b></span>')
    if m.link:
        chips.append(f'<span class="chip">🔗 <a href="{esc(m.link)}" target="_blank" rel="noopener">реплей матча</a></span>')
    return f'<div class="chips">{"".join(chips)}</div>'


def _leader_timeline(match: Match) -> str:
    changes = leader_changes(match)
    if not changes:
        return ""
    steps = "".join(
        f'<span class="step">ход <b>{t}</b>: {esc(name)}</span>' for t, name in changes
    )
    return f'<h2>Хронология лидерства</h2><div class="timeline">{steps}</div>'


def _awards_block(match: Match) -> str:
    items = awards(match)
    if not items:
        return ""
    cards = "".join(
        f'<div class="award"><div class="title">{esc(a["title"])}</div>'
        f'<div class="who">{esc(a["player"])}</div>'
        f'<div class="why">{esc(a["comment"])}</div></div>'
        for a in items
    )
    return f'<h2>Награды</h2><div class="awards">{cards}</div>'


def _chart_block(div_id: str, title: str = "") -> str:
    return f'<div class="panel"><div id="{div_id}" style="width:100%;height:360px"></div></div>'


# ---------- сборка ----------

def build_report(match: Match, plotly_js=_AUTO) -> str:
    """Собирает самостоятельный HTML-отчёт.

    plotly_js: строка — встроить JS; None — подключить через CDN;
    по умолчанию — встроенный файл из пакета, при отсутствии CDN.
    """
    if plotly_js is _AUTO:
        plotly_js = load_bundled_plotly()
    match.validate()

    figs = _build_figures(match)
    figs_json = (json.dumps(figs, ensure_ascii=False)
                   .replace("<", r"\u003c").replace(">", r"\u003e")
                   .replace("&", r"\u0026"))

    if plotly_js:
        plotly_tag = f"<script>{plotly_js}</script>"
    else:
        plotly_tag = f'<script src="{_PLOTLY_CDN}" charset="utf-8"></script>'

    title = match.meta.title or "Матч The Battle of Polytopia"
    generated = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [
        "<!DOCTYPE html>",
        '<html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(title)} — анализ матча</title>",
        f"<style>{_CSS}</style>",
        plotly_tag,
        '</head><body><div class="wrap">',
        f"<h1>⚔️ {esc(title)}</h1>",
        '<div class="subtitle">Итоговый анализ матча — The Battle of Polytopia</div>',
        _meta_chips(match),
        _winner_banner(match),
        f'<h2>Итоговая таблица</h2><div class="panel">{_standings_table(match)}</div>',
        f"<h2>Динамика счёта</h2>{_chart_block('chart-score')}",
        _leader_timeline(match),
        f'<h2>Экономика и развитие</h2><div class="grid2">'
        f"{_chart_block('chart-income')}{_chart_block('chart-cities')}</div>",
        f'<h2>Армия</h2><div class="grid2">'
        f"{_chart_block('chart-army')}{_chart_block('chart-ranks')}</div>",
        f'<h2>Боевые действия</h2><div class="grid2">'
        f"{_chart_block('chart-kills')}{_chart_block('chart-losses')}</div>",
        _chart_block("chart-combat"),
        _awards_block(match),
        f"<footer>Сгенерировано polytopia-match-analyzer v{__version__} · {generated}</footer>",
        "</div>",
        "<script>",
        f"const FIGS = {figs_json};",
        "for (const [id, fig] of Object.entries(FIGS)) {",
        "  Plotly.newPlot(id, fig.data, fig.layout, {responsive: true, displayModeBar: false});",
        "}",
        "</script>",
        "</body></html>",
    ]
    return "\n".join(parts)
