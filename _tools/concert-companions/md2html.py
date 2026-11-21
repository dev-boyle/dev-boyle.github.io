#!/usr/bin/env python3
"""Convert an Obsidian concert-companion .md into a themed HTML page in the
classical-companions folder of the dev-boyle.github.io repo, then rebuild the
companions index from every page found there.

Usage: md2html.py <input.md> <repo-root> [--slug SLUG] [--date YYYY-MM-DD]
"""
import argparse, glob, html, os, re, sys
from datetime import datetime

import markdown
import yaml

CALLOUT_TITLES = {
    'abstract': 'At a glance', 'summary': 'At a glance', 'tip': 'Tip', 'note': 'Note',
    'important': 'Important', 'seealso': 'See also', 'info': 'Info', 'warning': 'Note',
    'quote': '', 'example': 'Example',
}
NBSP = ' '


def split_frontmatter(text):
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.S)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        fm = {}
    return fm, text[m.end():]


def ascii_fold(t):
    import unicodedata
    return unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()


def slugify(name):
    name = os.path.splitext(os.path.basename(name))[0]
    name = re.sub(r'^[0-9a-f]{8}-(?=\d{4}-)', '', name)  # upload prefix
    name = ascii_fold(name).lower().replace('&', 'and')
    name = re.sub(r'[^a-z0-9]+', '-', name).strip('-')
    return name


def title_from_filename(name):
    """Obsidian uses the file name as the title; characters that are illegal in file
    names were replaced by '_' ('___' stands for ' & ' or ' \u2013 ', '__' for ', ')."""
    base = os.path.splitext(os.path.basename(name))[0]
    base = re.sub(r'^[0-9a-f]{8}-(?=\d{4}-)', '', base)
    parts = base.split('_-_')
    if parts and re.match(r'\d{4}-\d{2}-\d{2}$', parts[0]):
        parts = parts[1:]

    def fix(p, first=False):
        n = p.count('___')
        if n >= 2:                       # first one separates "Org: Program" (or "Program \u2013 Pieces" in a later part)
            p = p.replace('___', ': ' if first else ' \u2013 ', 1)
        p = p.replace('___', ' & ').replace('__', ', ').replace('_', ' ')
        return p.strip()
    parts = [fix(p, i == 0) for i, p in enumerate(parts)]
    if not parts:
        return base
    # "Organisation: Performer \u2013 Program" (first separator is a colon, later ones an en dash)
    return parts[0] + (': ' + ' \u2013 '.join(parts[1:]) if len(parts) > 1 else '')


def time_minutes(t):
    """Start time in minutes after midnight ('6:00\u20137:00 pm' -> 1080); 0 if unknown. Used to order same-day concerts."""
    m = re.search(r'(\d{1,2})(?::(\d\d))?', t or '')
    ap = re.findall(r'([ap])\.?m', (t or '').lower())
    if not m:
        return 0
    h = int(m.group(1)) % 12 + (12 if ap and ap[-1] == 'p' else 0)
    return h * 60 + int(m.group(2) or 0)


def parse_date(s):
    s = (s or '').strip()
    for fmt in ('%A, %B %d, %Y', '%B %d, %Y', '%A, %b %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return None


def pull_callouts(body):
    """Replace Obsidian callouts with tokens; return new body and list of html."""
    lines = body.split('\n')
    out, callouts, i = [], [], 0
    md = markdown.Markdown(extensions=['tables', 'sane_lists', 'nl2br'])
    while i < len(lines):
        if lines[i].startswith('>'):
            j = i
            while j < len(lines) and lines[j].startswith('>'):
                j += 1
            block = [re.sub(r'^> ?', '', l) for l in lines[i:j]]
            m = re.match(r'^\[!(\w+)\][+-]?\s*(.*)$', block[0].strip())
            if m:
                kind, title = m.group(1).lower(), m.group(2).strip()
                if not title:
                    title = CALLOUT_TITLES.get(kind, kind.capitalize())
                inner = md.reset().convert('\n'.join(block[1:]).strip('\n'))
                head = f'<div class="callout-title">{html.escape(title)}</div>' if title else ''
                callouts.append(f'<aside class="callout c-{kind}">{head}{inner}</aside>')
                out += ['', f'@@CALLOUT{len(callouts)-1}@@', '']
            else:
                out += lines[i:j]
            i = j
        else:
            out.append(lines[i])
            i += 1
    return '\n'.join(out), callouts


def autolink(body):
    def lab(u):
        return 'Apple Music Classical' if 'classical.music.apple.com' in u else (
            'Apple Music' if 'music.apple.com' in u else re.sub(r'^https?://(www\.)?', '', u).split('/')[0])
    def sub(m):
        u = m.group(0)
        tail = ''
        while u and u[-1] in '.,;:!?':
            tail, u = u[-1] + tail, u[:-1]
        return '[%s](%s)%s' % (lab(u), u, tail)
    return re.sub(r'(?<![\]\(<"\'=])\bhttps?://[^\s<>)\]]+', sub, body)


def mark_tasks(body):
    """List items that were checkboxes (the box itself is often lost in a paste
    and leaves a leading no-break space, or a real [ ] / [x]) become checkboxes."""
    res = []
    for l in body.split('\n'):
        m = re.match(r'^(\s*[-*])\s*(\[( |x|X)\]|' + NBSP + r')\s*(.+)$', l)
        if m:
            res.append(f'{m.group(1)} <label class="cb"><input type="checkbox"> {m.group(4).strip()}</label>')
        else:
            res.append(l)
    return '\n'.join(res)


def chip_label(heading, work=False):
    h = re.sub(r'[*_`]', '', heading).strip()
    low = h.lower()
    fixed = [('listen before', 'Listen'), ('emotional', 'Journey'), ('about the ensemble', 'Ensemble'),
             ('themes running', 'Themes'), ('final reflection', 'Reflection'), ('musical connections', 'Connections'),
             ('spoken word', 'Spoken Word')]
    for k, v in fixed:
        if low.startswith(k) or k in low[:30]:
            return v
    if ' \u2014 ' in h or ' - ' in h:
        left, right = re.split(r" \u2014 | - ", h, maxsplit=1)
        lw = left.strip().split()
        name = ' '.join(lw[-2:]) if len(lw) > 1 and len(lw[-1]) <= 2 else lw[-1]   # 'Pura F\u00e9', not 'F\u00e9'
        if work:
            w = right.strip().split()
            k = 2 if (len(w) > 1 and (w[0].isdigit() or len(w[0]) <= 2)) else 1
            return name + ' \u00b7 ' + re.sub(r'[^\w\u00c0-\u024f]+$', '', ' '.join(w[:k]))
        return name
    # plain headings: keep chips short ("About the Venue" -> "Venue", "Tradition: Behind..." -> "Tradition")
    h = h.split(':')[0].split(',')[0].strip()
    h = re.sub(r'^(About (the )?|What to )', '', h, flags=re.I).strip()
    if len(h) > 20:
        h = ' '.join(h.split()[:2])
    return h


def render(md_text, slug, filename, date_override=None, title_override=None):
    fm, body = split_frontmatter(md_text)
    body = body.replace('\r\n', '\n')
    title = str(fm.get('title') or '')
    m = re.search(r'^# (.+)$', body, re.M)
    head_zone = body[:m.start()] if m else ''
    if m and len(head_zone.strip()) < 40 and (not title or m.group(1).strip() == title.strip()):
        title = title or m.group(1).strip()
        body = body[:m.start()] + body[m.end():]
    title = re.sub(r'\s*[\u2013\u2014-]\s*Concert Companion\s*$', '', title, flags=re.I).strip()
    if title and ': ' not in title:
        title = re.sub(r'^(.+?) [-\u2013\u2014] (.+)$', r'\1: \2', title, count=1)   # "Org - Program" -> "Org: Program"
    if title_override:
        title = title_override
    elif not title:
        title = title_from_filename(filename)

    meta = {}
    for key in ('Date', 'Time', 'Location'):
        mm = re.search(r'^\*\*' + key + r'\*\*:\s*(.+?)\s*$', body, re.M)
        if mm:
            meta[key] = mm.group(1)
            body = body[:mm.start()] + body[mm.end():]
    if 'Date' not in meta:
        # header written as a bold "Day, Month D, YYYY @ time" line (venue after it on the same
        # line or on the next line)
        mm = re.match(r'\s*\*\*([^*\n]*\d{4}[^*\n]*)\*\*[ \t]*([^\n]*)\n(?:([^\n*#>|\-\d][^\n]*)\n)?', body)
        if mm:
            when = mm.group(1).strip()
            parts_ = re.split(r'\s*[@\u00b7|]\s*|\s+at\s+', when, maxsplit=1)
            meta['Date'] = parts_[0].strip()
            if len(parts_) > 1:
                meta['Time'] = parts_[1].strip()
            venue = (mm.group(2) or '').strip() or (mm.group(3) or '').strip()
            venue = re.sub(r'^[\u2014\u2013-]\s*', '', venue)
            pm = re.match(r'^(\([^)]*\))\s*(.*)$', venue)          # "(doors at 9:30 am) Venue, ..." belongs to the time
            if pm:
                meta['Time'] = (meta.get('Time', '') + ' ' + pm.group(1)).strip()
                venue = pm.group(2)
            if venue:
                meta['Location'] = venue
            body = body[mm.end():]
    body = re.sub(r'^\*\*(Performers|Program)\*\*[ \t]*$', r'## \1', body, flags=re.M)
    iso = date_override or parse_date(meta.get('Date')) or \
        (re.match(r'(\d{4}-\d{2}-\d{2})', os.path.basename(filename)) or [None, None])[1]
    body = re.sub(r'^\s*---+\s*$', '', body, flags=re.M)

    body = autolink(body)
    body = mark_tasks(body)            # before NBSP clean-up: a bare NBSP marks a lost checkbox
    body = body.replace(NBSP, ' ')
    body, callouts = pull_callouts(body)

    # split into sections on level-1 headings
    parts = re.split(r'^# (.+)$', body, flags=re.M)
    first_label = 'Program' if re.search(r'^#{2,3} Program', parts[0], re.M) else 'Overview'
    sections = [(first_label, first_label.lower(), parts[0])]
    labels = [chip_label(parts[k]) for k in range(1, len(parts), 2)]
    for k in range(1, len(parts), 2):
        h = parts[k].strip()
        sections.append((h, None, parts[k + 1]))

    used, chips, html_sections = set(), [], []
    for h, sid, text in sections:
        label = first_label if sid == first_label.lower() else (
            chip_label(h, work=labels.count(chip_label(h)) > 1))
        if sid is None:
            base = re.sub(r'[^a-z0-9]+', '-', ascii_fold(label).lower()).strip('-')[:30]
            sid, n = base or 's', 2
            while sid in used:
                sid, n = f'{base}-{n}', n + 1
        used.add(sid)
        md = markdown.Markdown(extensions=['tables', 'sane_lists', 'nl2br'])
        inner = md.convert(text.strip('\n'))
        inner = re.sub(r'<h3>', '<h4>', inner).replace('</h3>', '</h4>')
        inner = re.sub(r'<h2>', '<h3>', inner).replace('</h2>', '</h3>')
        for c, ctext in enumerate(callouts):
            inner = inner.replace(f'<p>@@CALLOUT{c}@@</p>', ctext)
        inner = listen_buttons(inner)
        if sid == first_label.lower() and not inner.strip():
            continue
        head = '' if sid == first_label.lower() else f'  <h2>{md.convert(h)[3:-4]}</h2>\n'
        html_sections.append(
            f'<section id="{sid}">\n{head}{inner}\n  <a class="top" href="#top">↑ Back to top</a>\n</section>\n')
        chips.append((sid, label))

    chips_html = '\n'.join(f'    <a href="#{s}">{html.escape(l)}</a>' for s, l in chips)
    loc = meta.get('Location', '')
    if loc and not re.search(r'\s[\u2014\u2013]\s', loc):
        m_ = re.match(r'^([^\d]+?),\s*(\d.*)$', loc)          # "Venue, 123 Street" -> "Venue \u2014 123 Street"
        if m_:
            meta['Location'] = m_.group(1) + ' \u2014 ' + m_.group(2)
    sub_bits = [x for x in (meta.get('Date'), meta.get('Time'), meta.get('Location')) if x]
    sub = ' · '.join(html.escape(x) for x in sub_bits)
    page = TEMPLATE
    page = page.replace('@@CSS@@', load_css())
    org, sep, prog = title.partition(': ')
    if not sep:
        org, prog = 'Concert companion', title
    page = page.replace('@@KICKER@@', html.escape(org))
    page = page.replace('@@H1@@', html.escape(prog))
    page = page.replace('@@TITLE@@', html.escape(title))
    page = page.replace('@@SUB@@', sub)
    page = page.replace('@@MINUTES@@', str(time_minutes(meta.get('Time', ''))))
    page = page.replace('@@CHIPS@@', chips_html)
    page = page.replace('@@SECTIONS@@', '\n'.join(html_sections))
    page = page.replace('@@ISO@@', iso or '')
    page = page.replace('@@VENUE@@', html.escape(re.split(r'\s+[\u2014\u2013]\s+', meta.get('Location', ''))[0]))
    page = page.replace('@@DATELABEL@@', html.escape(meta.get('Date', '')))
    page = re.sub(r'<a\b([^>]*?\bhref="https?://[^"]+"[^>]*)>',
                  lambda m: m.group(0) if 'target=' in m.group(1) else '<a%s target="_blank" rel="noopener">' % m.group(1),
                  page)
    return page, title, iso


def listen_buttons(inner):
    """A paragraph that starts with the headphone emoji becomes listen buttons."""
    def conv(m):
        links = re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', m.group(1))
        rest = re.sub(r'<a href="[^"]+"[^>]*>.*?</a>', '', m.group(1), flags=re.S)
        if not links or re.sub(r'[\s\u00b7|,]+', '', rest):
            return m.group(0)
        btns = ''.join('<a%s href="%s">\u25b6 %s</a>' % ('' if i == 0 else ' class="alt"', u, t)
                       for i, (u, t) in enumerate(links))
        return '<div class="listen">%s</div>' % btns
    return re.sub(r'<p>\U0001F3A7\s*(.*?)</p>', conv, inner, flags=re.S)


def load_css():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'companion.css')
    return open(p, encoding='utf-8').read()


def rebuild_index(root):
    folder = os.path.join(root, 'classical-companions')
    items = []
    for f in glob.glob(os.path.join(folder, '*', 'index.html')):
        t = open(f, encoding='utf-8').read()
        g = lambda n: (re.search(r'<meta name="%s" content="([^"]*)"' % n, t) or [None, ''])[1]
        items.append((g('concert-date'), g('concert-title'), g('concert-label'), g('concert-venue'),
                      os.path.basename(os.path.dirname(f)), int(g('concert-minutes') or 0)))
    items.sort(key=lambda x: (x[0], x[5]), reverse=True)     # newest first; later start first on the same day
    idx_path = os.path.join(folder, 'index.html')
    idx = open(idx_path, encoding='utf-8').read()
    if items:
        def split(t):
            o, sp, p = html.unescape(t).partition(': ')
            return (f'<i>{html.escape(o)}</i><b>{html.escape(p)}</b>' if sp else f'<b>{t}</b>')
        lis = '\n'.join(
            f'    <li><a href="{s}/">{split(t)}<span>{" · ".join(x for x in (lab, v) if x)}</span></a></li>'
            for d, t, lab, v, s, _m in items)
        block = f'<ul>\n{lis}\n  </ul>'
    else:
        block = '<div class="empty">None yet.</div>'
    idx = re.sub(r'(<p class="desc">[^<]*</p>\s*)(<div class="empty">.*?</div>|<ul>.*?</ul>)',
                 lambda m: m.group(1) + block, idx, flags=re.S)
    open(idx_path, 'w', encoding='utf-8').write(idx)
    return len(items)


TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>@@TITLE@@ — Concert Companion</title>
<meta name="concert-title" content="@@TITLE@@">
<meta name="concert-date" content="@@ISO@@">
<meta name="concert-label" content="@@DATELABEL@@">
<meta name="concert-minutes" content="@@MINUTES@@">
<meta name="concert-venue" content="@@VENUE@@">
<style>
@@CSS@@
</style>
</head>
<body>

<header id="top">
  <a class="back" href="../">← All companions</a>
  <div class="kicker">@@KICKER@@</div>
  <h1>@@H1@@</h1>
  <p class="sub">@@SUB@@</p>
</header>

<nav class="bar" aria-label="Guide outline">
  <div class="chips">
@@CHIPS@@
  </div>
</nav>

<main>

@@SECTIONS@@
</main>

<footer>
  <a class="back" href="../">← All companions</a>
</footer>

<script>
  // Highlight the current section in the sticky bar.
  var links = {};
  document.querySelectorAll('nav.bar a').forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) {
        Object.values(links).forEach(function (a) { a.classList.remove('on'); });
        var a = links[e.target.id];
        if (a) { a.classList.add('on'); a.scrollIntoView({ inline: 'center', block: 'nearest' }); }
      }
    });
  }, { rootMargin: '-20% 0px -70% 0px' });
  document.querySelectorAll('main section').forEach(function (s) { io.observe(s); });
  // Remember checklist ticks on this device (best effort).
  var boxes = document.querySelectorAll('input[type=checkbox]');
  var key = 'cb:' + location.pathname;
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch (e) {}
  boxes.forEach(function (b, i) {
    if (saved[i]) b.checked = true;
    b.addEventListener('change', function () {
      saved[i] = b.checked;
      try { localStorage.setItem(key, JSON.stringify(saved)); } catch (e) {}
    });
  });
</script>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('md')
    ap.add_argument('root')
    ap.add_argument('--slug')
    ap.add_argument('--date')
    ap.add_argument('--title')
    a = ap.parse_args()
    text = open(a.md, encoding='utf-8').read()
    slug = a.slug or slugify(a.md)
    page, title, iso = render(text, slug, a.md, a.date, a.title)
    out = os.path.join(a.root, 'classical-companions', slug)
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'index.html'), 'w', encoding='utf-8').write(page)
    n = rebuild_index(a.root)
    print(f'wrote {out}/index.html ({len(page)} bytes); title={title!r} date={iso}; index has {n} companion(s)')


if __name__ == '__main__':
    main()
