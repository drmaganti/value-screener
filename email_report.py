"""
email_report.py - builds the weekly digest as a modern, minimalist HTML email
(with a plain-text fallback for clients that strip HTML).

Email HTML is its own discipline: inline styles only (Gmail strips <style>),
a centered max-width container, system fonts, no external images. The design is
deliberately restrained -- lots of whitespace, one accent colour, green/red only
for returns.
"""
from datetime import date

# ── palette / type ──────────────────────────────────────────────────────────
INK = "#111827"        # near-black text
MUTE = "#6b7280"       # muted grey
LINE = "#e5e7eb"       # hairline borders
BG = "#f4f5f7"         # page background
CARD = "#ffffff"       # card background
ACCENT = "#2563eb"     # blue accent
POS = "#0f9d58"        # green (beat / up)
NEG = "#d93025"        # red (down)
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _pill(score):
    return (f'<span style="display:inline-block;background:{ACCENT};color:#fff;'
            f'font-weight:700;font-size:14px;padding:4px 12px;border-radius:999px;">'
            f'{score:.0f}<span style="opacity:.7;font-weight:500"> / 100</span></span>')


def _metric(label, value):
    return (f'<td style="padding:0 14px 0 0;vertical-align:top;">'
            f'<div style="font-size:11px;color:{MUTE};text-transform:uppercase;letter-spacing:.4px;">{label}</div>'
            f'<div style="font-size:15px;color:{INK};font-weight:600;margin-top:2px;">{value}</div></td>')


def _nan(x):
    return x is None or (isinstance(x, float) and x != x)


def _featured_card(c, rank):
    f, t, v = c.fund, c.tech, c.val
    val_txt = (f"{v.pe_percentile*100:.0f}th %ile P/E" if not _nan(v.pe_percentile)
               else (f"{v.yield_vs_norm:.2f}x avg yield" if not _nan(v.yield_vs_norm) else "cheap"))
    metrics = (_metric("Pullback", f"{t.pullback_pct*100:.0f}%")
               + _metric("RSI", f"{t.rsi:.0f}")
               + _metric("Valuation", val_txt)
               + _metric("Health", f"{c.qual.fscore}/9")
               + _metric("Catalyst", c.cat.category.replace("_", " ")))
    return f"""
    <tr><td style="padding:0 24px;">
      <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};border-radius:12px;margin-bottom:16px;">
        <tr><td style="padding:18px 20px 14px;">
          <table width="100%"><tr>
            <td style="vertical-align:middle;">
              <span style="font-size:20px;font-weight:700;color:{INK};">{f.ticker}</span>
              <span style="font-size:13px;color:{MUTE};margin-left:8px;">{f.exchange} &middot; {f.sector}</span>
            </td>
            <td align="right" style="vertical-align:middle;">{_pill(c.composite)}</td>
          </tr></table>
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;"><tr>{metrics}</tr></table>
          <p style="font-size:14px;line-height:1.6;color:#374151;margin:14px 0 2px;">{c.analysis}</p>
        </td></tr>
      </table>
    </td></tr>"""


def _other_row(c):
    return (f'<tr><td style="padding:6px 0;border-bottom:1px solid {LINE};">'
            f'<span style="font-weight:600;color:{INK};">{c.fund.ticker}</span>'
            f'<span style="color:{MUTE};font-size:13px;"> &middot; {c.fund.sector}</span></td>'
            f'<td align="right" style="padding:6px 0;border-bottom:1px solid {LINE};color:{INK};font-weight:600;">'
            f'{c.composite:.0f}</td></tr>')


def _ret(v):
    if v is None:
        return f'<span style="color:{MUTE};">&mdash;</span>'
    color = POS if v >= 0 else NEG
    return f'<span style="color:{color};font-weight:600;">{v:+.1f}%</span>'


def _record_row(r):
    o = r["outcomes"]
    return (f'<tr>'
            f'<td style="padding:7px 0;border-bottom:1px solid {LINE};font-weight:600;color:{INK};">{r["ticker"]}</td>'
            f'<td style="padding:7px 0;border-bottom:1px solid {LINE};color:{MUTE};font-size:13px;">{r["pick_date"]}</td>'
            f'<td align="right" style="padding:7px 0;border-bottom:1px solid {LINE};">{_ret(o.get("1w"))}</td>'
            f'<td align="right" style="padding:7px 0;border-bottom:1px solid {LINE};">{_ret(o.get("1m"))}</td>'
            f'<td align="right" style="padding:7px 0;border-bottom:1px solid {LINE};">{_ret(o.get("3m"))}</td>'
            f'</tr>')


def build(today, featured, qualifiers, all_picks, stats):
    others = qualifiers[len(featured):]
    # Track record = picks from previous runs (anything not dated today).
    historic = [r for r in all_picks if r["pick_date"] != today.isoformat()]
    historic = sorted(historic, key=lambda r: r["pick_date"], reverse=True)[:15]

    # ── header ──
    parts = [f"""\
<!DOCTYPE html><html><body style="margin:0;padding:0;background:{BG};">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{BG};padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:{CARD};border-radius:16px;overflow:hidden;font-family:{FONT};">
  <tr><td style="padding:28px 24px 8px;">
    <div style="font-size:12px;letter-spacing:1.5px;color:{ACCENT};text-transform:uppercase;font-weight:700;">Weekly Value Screen</div>
    <div style="font-size:13px;color:{MUTE};margin-top:4px;">{today.strftime('%A, %B %d, %Y')}</div>
  </td></tr>"""]

    # ── this week ──
    parts.append(f'<tr><td style="padding:18px 24px 6px;"><div style="font-size:16px;font-weight:700;color:{INK};">This Week\'s Top Picks</div></td></tr>')
    if featured:
        for i, c in enumerate(featured, 1):
            parts.append(_featured_card(c, i))
    else:
        parts.append(f'<tr><td style="padding:0 24px 12px;"><p style="font-size:14px;color:{MUTE};">No names cleared the value bar this week. Cash is a position.</p></td></tr>')

    # ── other qualifiers ──
    if others:
        rows = "".join(_other_row(c) for c in others)
        parts.append(f"""
        <tr><td style="padding:8px 24px 4px;">
          <div style="font-size:13px;font-weight:600;color:{MUTE};text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px;">Also cleared the bar</div>
          <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
        </td></tr>""")

    # ── track record ──
    parts.append(f'<tr><td style="padding:22px 24px 6px;"><div style="font-size:16px;font-weight:700;color:{INK};">Track Record</div></td></tr>')
    if stats:
        parts.append(f"""
        <tr><td style="padding:0 24px 10px;">
          <p style="font-size:14px;line-height:1.6;color:#374151;margin:0;">
          Of {stats['n']} picks now past one month, <b>{stats['beats']} beat the S&amp;P</b>.
          Average move <b style="color:{POS if stats['avg']>=0 else NEG};">{stats['avg']:+.1f}%</b>
          vs the S&amp;P's {stats['avg_bench']:+.1f}% over the same windows.</p>
        </td></tr>""")
    if historic:
        body = "".join(_record_row(r) for r in historic)
        parts.append(f"""
        <tr><td style="padding:0 24px 8px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="font-size:11px;color:{MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">Ticker</td>
              <td style="font-size:11px;color:{MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">Picked</td>
              <td align="right" style="font-size:11px;color:{MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">1W</td>
              <td align="right" style="font-size:11px;color:{MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">1M</td>
              <td align="right" style="font-size:11px;color:{MUTE};text-transform:uppercase;letter-spacing:.4px;padding-bottom:4px;">3M</td>
            </tr>{body}
          </table>
        </td></tr>""")
    else:
        parts.append(f'<tr><td style="padding:0 24px 12px;"><p style="font-size:14px;color:{MUTE};">No matured picks yet -- returns appear here as past picks age.</p></td></tr>')

    # ── footer ──
    parts.append(f"""
  <tr><td style="padding:18px 24px 26px;border-top:1px solid {LINE};">
    <p style="font-size:12px;line-height:1.6;color:{MUTE};margin:0;">
      Research tool, not investment advice. Every name here is screening output, not a recommendation &mdash;
      verify the story yourself before acting. Returns are price-only and exclude dividends and fees.</p>
  </td></tr>
</table></td></tr></table></body></html>""")

    return "".join(parts), _plain_text(today, featured, others, historic, stats)


def _plain_text(today, featured, others, historic, stats):
    L = [f"WEEKLY VALUE SCREEN - {today.strftime('%A, %B %d, %Y')}", "=" * 56, "", "THIS WEEK'S TOP PICKS", ""]
    if featured:
        for i, c in enumerate(featured, 1):
            L += [f"{i}. {c.fund.ticker} ({c.fund.exchange}, {c.fund.sector}) - {c.composite:.0f}/100",
                  f"   Pullback {c.tech.pullback_pct*100:.0f}%  RSI {c.tech.rsi:.0f}  F-score {c.qual.fscore}/9  [{c.cat.category}]",
                  "", f"   {c.analysis}", ""]
    else:
        L.append("No names cleared the bar this week.")
    if others:
        L += ["", "ALSO CLEARED THE BAR:"] + [f"   {c.fund.ticker}  {c.composite:.0f}/100  ({c.fund.sector})" for c in others]
    L += ["", "TRACK RECORD"]
    if stats:
        L.append(f"   {stats['beats']}/{stats['n']} past-month picks beat the S&P; avg {stats['avg']:+.1f}% vs {stats['avg_bench']:+.1f}%.")
    for r in historic:
        o = r["outcomes"]
        fmt = lambda x: f"{x:+.1f}%" if x is not None else "--"
        L.append(f"   {r['ticker']:8} picked {r['pick_date']}  1W {fmt(o.get('1w'))}  1M {fmt(o.get('1m'))}  3M {fmt(o.get('3m'))}")
    L += ["", "-" * 56, "Research tool, not investment advice. Verify before acting."]
    return "\n".join(L)
