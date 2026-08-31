"""V2 weekly email with strategy-separated, benchmark-correct reporting."""
from __future__ import annotations

import email_report as base
import reporting_integrity as integrity


def _strategy_card(label, stats):
    title = "V2 live strategy" if label == "v2" else "V1 tracked cohort"
    n = stats.get("comparable_n", 0)
    if not n:
        return (
            f'<div style="padding:10px 0;font-size:14px;color:{base.MUTE};">'
            f'<b style="color:{base.INK};">{title}:</b> no one-month picks with '
            f'valid benchmark data yet.</div>'
        )
    beat_rate = stats["beat_rate"]
    excess = stats["avg_excess"]
    median = stats["median_excess"]
    color = base.POS if excess is not None and excess >= 0 else base.NEG
    return f"""
    <div style="padding:10px 0;border-bottom:1px solid {base.LINE};font-size:14px;line-height:1.6;color:#374151;">
      <b style="color:{base.INK};">{title}</b><br>
      {stats['beats']} of {n} picks beat their benchmark ({beat_rate:.1f}%).
      Average excess return <b style="color:{color};">{excess:+.1f} pts</b>;
      median excess {median:+.1f} pts.
      <span style="color:{base.MUTE};">Average pick return {stats['avg_return']:+.1f}% vs benchmark {stats['avg_benchmark']:+.1f}%.</span>
    </div>"""


def _record_row(record):
    outcomes, _ = integrity.outcome_maps_for_reporting(record)
    version = integrity.strategy_bucket(record).replace("pre_v1", "legacy")
    return (
        f'<tr>'
        f'<td style="padding:7px 0;border-bottom:1px solid {base.LINE};font-weight:600;color:{base.INK};">{record["ticker"]}</td>'
        f'<td style="padding:7px 0;border-bottom:1px solid {base.LINE};color:{base.MUTE};font-size:12px;">{version}</td>'
        f'<td style="padding:7px 0;border-bottom:1px solid {base.LINE};color:{base.MUTE};font-size:13px;">{record["pick_date"]}</td>'
        f'<td align="right" style="padding:7px 0;border-bottom:1px solid {base.LINE};">{base._ret(outcomes.get("1w"))}</td>'
        f'<td align="right" style="padding:7px 0;border-bottom:1px solid {base.LINE};">{base._ret(outcomes.get("1m"))}</td>'
        f'<td align="right" style="padding:7px 0;border-bottom:1px solid {base.LINE};">{base._ret(outcomes.get("3m"))}</td>'
        f'</tr>'
    )


def build(today, featured, qualifiers, all_picks, stats_by_strategy):
    others = qualifiers[len(featured):]
    historic = [r for r in all_picks if r.get("pick_date") != today.isoformat()]
    historic = sorted(historic, key=lambda r: r.get("pick_date", ""), reverse=True)[:15]

    parts = [f"""\
<!DOCTYPE html><html><body style="margin:0;padding:0;background:{base.BG};">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{base.BG};padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:{base.CARD};border-radius:16px;overflow:hidden;font-family:{base.FONT};">
  <tr><td style="padding:28px 24px 8px;">
    <div style="font-size:12px;letter-spacing:1.5px;color:{base.ACCENT};text-transform:uppercase;font-weight:700;">Weekly Value Screen V2</div>
    <div style="font-size:13px;color:{base.MUTE};margin-top:4px;">{today.strftime('%A, %B %d, %Y')}</div>
  </td></tr>"""]

    parts.append(f'<tr><td style="padding:18px 24px 6px;"><div style="font-size:16px;font-weight:700;color:{base.INK};">This Week\'s Top Picks</div></td></tr>')
    if featured:
        for i, candidate in enumerate(featured, 1):
            parts.append(base._featured_card(candidate, i))
    else:
        parts.append(f'<tr><td style="padding:0 24px 12px;"><p style="font-size:14px;color:{base.MUTE};">No names cleared the V2 value and stabilization gates this week.</p></td></tr>')

    if others:
        rows = "".join(base._other_row(c) for c in others)
        parts.append(f"""
        <tr><td style="padding:8px 24px 4px;">
          <div style="font-size:13px;font-weight:600;color:{base.MUTE};text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px;">Also cleared the bar</div>
          <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
        </td></tr>""")

    parts.append(f'<tr><td style="padding:22px 24px 6px;"><div style="font-size:16px;font-weight:700;color:{base.INK};">Track Record</div></td></tr>')
    v2_stats = stats_by_strategy.get("v2", {})
    v1_stats = stats_by_strategy.get("v1", {})
    parts.append(f'<tr><td style="padding:0 24px 10px;">{_strategy_card("v2", v2_stats)}{_strategy_card("v1", v1_stats)}'
                 f'<p style="font-size:12px;line-height:1.5;color:{base.MUTE};margin:10px 0 0;">V1 figures use corrected exact-horizon returns and an appropriate benchmark (SPY for US, XIU.TO for TSX). Original emailed V1 outcome values remain preserved in the repository for audit. Pre-V1 legacy picks are excluded from the V1-vs-V2 comparison.</p></td></tr>')

    if historic:
        body = "".join(_record_row(r) for r in historic)
        parts.append(f"""
        <tr><td style="padding:0 24px 8px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="font-size:11px;color:{base.MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">Ticker</td>
              <td style="font-size:11px;color:{base.MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">Version</td>
              <td style="font-size:11px;color:{base.MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">Picked</td>
              <td align="right" style="font-size:11px;color:{base.MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">1W</td>
              <td align="right" style="font-size:11px;color:{base.MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">1M</td>
              <td align="right" style="font-size:11px;color:{base.MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">3M</td>
            </tr>{body}
          </table>
        </td></tr>""")

    parts.append(f"""
  <tr><td style="padding:18px 24px 26px;border-top:1px solid {base.LINE};">
    <p style="font-size:12px;line-height:1.6;color:{base.MUTE};margin:0;">
      Research tool, not investment advice. Returns are price-only and exclude dividends and fees.
      Performance uses only picks with valid benchmark data; manual/test scans are not part of the official track record.</p>
  </td></tr>
</table></td></tr></table></body></html>""")

    return "".join(parts), _plain_text(today, featured, others, historic, stats_by_strategy)


def _plain_text(today, featured, others, historic, stats_by_strategy):
    lines = [
        f"WEEKLY VALUE SCREEN V2 - {today.strftime('%A, %B %d, %Y')}",
        "=" * 64,
        "",
        "THIS WEEK'S TOP PICKS",
        "",
    ]
    if featured:
        for i, c in enumerate(featured, 1):
            lines += [
                f"{i}. {c.fund.ticker} ({c.fund.exchange}, {c.fund.sector}) - {c.composite:.0f}/100",
                f"   Pullback {c.tech.pullback_pct*100:.0f}%  RSI {c.tech.rsi:.0f}  Value {c.val.score:.0f}/100  Health {c.qual.fscore}/9  [{c.cat.category}]",
                f"   {c.analysis}",
                "",
            ]
    else:
        lines.append("No names cleared the V2 value and stabilization gates this week.")
    if others:
        lines += ["", "ALSO CLEARED THE BAR:"] + [
            f"   {c.fund.ticker}  {c.composite:.0f}/100  ({c.fund.sector})"
            for c in others
        ]

    lines += ["", "TRACK RECORD"]
    for label, title in (("v2", "V2"), ("v1", "V1")):
        s = stats_by_strategy.get(label, {})
        n = s.get("comparable_n", 0)
        if n:
            lines.append(
                f"   {title}: {s['beats']}/{n} beat benchmark ({s['beat_rate']:.1f}%); "
                f"avg excess {s['avg_excess']:+.1f} pts; median excess {s['median_excess']:+.1f} pts."
            )
        else:
            lines.append(f"   {title}: no 1-month picks with valid benchmark data yet.")
    lines.append("   V1 uses corrected exact-horizon returns; pre-V1 legacy is excluded from V1-vs-V2 comparison.")

    fmt = lambda x: f"{x:+.1f}%" if x is not None else "--"
    for r in historic:
        outcomes, _ = integrity.outcome_maps_for_reporting(r)
        version = integrity.strategy_bucket(r).replace("pre_v1", "legacy")
        lines.append(
            f"   {r['ticker']:8} {version:6} picked {r['pick_date']}  "
            f"1W {fmt(outcomes.get('1w'))}  1M {fmt(outcomes.get('1m'))}  3M {fmt(outcomes.get('3m'))}"
        )
    lines += ["", "-" * 64, "Research tool, not investment advice."]
    return "\n".join(lines)
