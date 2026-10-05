"""Static UI audit engine: parses HTML + CSS, resolves a simplified cascade
(specificity, source order, !important, inline styles, inheritance) and runs checks.
Layout-dependent checks (touch-target size, overflow) need a real browser and stay client-side."""
import re
from collections import Counter

import tinycss2
from bs4 import BeautifulSoup, Comment, NavigableString
from tinycss2.color3 import parse_color

W = {"critical": 9, "major": 4, "minor": 1.5}
CATS = ["Accessibility", "Typography", "Layout", "Structure", "Consistency"]
SKIP = {"script", "style", "head", "title", "meta", "link", "br", "noscript", "template", "html"}
HEAD_EM = {"h1": 2, "h2": 1.5, "h3": 1.17, "h4": 1, "h5": .83, "h6": .67}
BOLD = {"h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "th"}
FORM = {"button", "input", "select", "textarea"}
KEYWORDS = {"xx-small": 9, "x-small": 10, "small": 13, "medium": 16, "large": 18, "x-large": 24, "xx-large": 32}
SPACING = {f"{k}{s}" for k in ("margin", "padding") for s in ("", "-top", "-right", "-bottom", "-left")}
VAGUE = re.compile(r"^(click here|here|read more|more|link|learn more|this)$", re.I)


# ---------- colour maths ----------
def _lin(c):
    c /= 255
    return c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4

def luminance(rgb):
    return .2126 * _lin(rgb[0]) + .7152 * _lin(rgb[1]) + .0722 * _lin(rgb[2])

def contrast(a, b):
    x, y = luminance(a), luminance(b)
    return (max(x, y) + .05) / (min(x, y) + .05)

def to_hex(rgb):
    return "#" + "".join(f"{round(v):02x}" for v in rgb[:3])

def blend(top, base):
    a = top[3]
    return tuple(top[i] * a + base[i] * (1 - a) for i in range(3)) + (1.0,)

def color_from(tokens):
    for t in tokens:
        if t.type in ("ident", "hash", "function"):
            c = parse_color(t)
            if c and c != "currentColor" and hasattr(c, "red"):
                return (c.red * 255, c.green * 255, c.blue * 255, c.alpha)
    return None

def length(t, base):
    if t.type == "dimension":
        v = t.value
        return {"px": v, "rem": v * 16, "em": v * base, "pt": v * 4 / 3}.get(t.lower_unit)
    if t.type == "percentage":
        return t.value / 100 * base
    if t.type == "number" and t.value == 0:
        return 0
    if t.type == "ident":
        return KEYWORDS.get(t.lower_value)
    return None


# ---------- cascade ----------
def _decls(content):
    out = []
    for d in tinycss2.parse_declaration_list(content, skip_comments=True, skip_whitespace=True):
        if d.type == "declaration":
            out.append((d.lower_name, [t for t in d.value if t.type != "whitespace"], d.important))
    return out

def _spec(sel):
    ids = len(re.findall(r"#[\w-]+", sel))
    cl = len(re.findall(r"\.[\w-]+|\[[^\]]*\]|(?<!:):[\w-]+", sel))
    tg = len(re.findall(r"(?:^|[\s>+~(])[a-zA-Z][\w-]*", sel))
    return (ids, cl, tg)

def _rules(soup):
    rules = []
    for st in soup.find_all("style"):
        for r in tinycss2.parse_stylesheet(st.get_text(), skip_comments=True, skip_whitespace=True):
            if r.type != "qualified-rule":
                continue
            decls = _decls(r.content)
            for s in tinycss2.serialize(r.prelude).split(","):
                if s.strip():
                    rules.append((s.strip(), decls))
    return rules

def _apply(cs, parent, n, v):
    if not v:
        return
    t0 = v[0]
    if n == "color":
        c = color_from(v)
        if c:
            cs["color"] = c
    elif n in ("background-color", "background"):
        txt = tinycss2.serialize(v).lower()
        if "gradient" in txt or "url(" in txt:
            cs["bg_image"] = True
        c = color_from(v)
        if c:
            cs["bg"] = c
    elif n == "font-size":
        for t in v:
            l = length(t, parent["size"])
            if l:
                cs["size"] = l
                break
    elif n == "font-family":
        cs["family"] = tinycss2.serialize(v).split(",")[0].strip(" \"'")
    elif n == "font-weight":
        if t0.type == "number":
            cs["weight"] = t0.value
        elif t0.type == "ident":
            cs["weight"] = 700 if t0.lower_value in ("bold", "bolder") else 400
    elif n == "line-height":
        cs["lh_tok"] = t0
    elif n in SPACING:
        cs["spd"][n] = v
    elif n in ("outline", "outline-style") and t0.type in ("ident", "number") and (
            getattr(t0, "lower_value", "") == "none" or getattr(t0, "value", 1) == 0):
        cs["outline_none"] = True

def _compute(el, parent, matched):
    cs = {k: parent[k] for k in ("color", "size", "family", "weight", "lh")}
    cs.update(bg=None, bg_image=False, outline_none=False, spd={}, lh_tok=None)
    tag = el.name
    if tag in HEAD_EM:
        cs["size"] = parent["size"] * HEAD_EM[tag]
    elif tag in ("small", "sub", "sup"):
        cs["size"] = parent["size"] * .83
    elif tag in FORM:
        cs.update(size=13.33, color=(0, 0, 0, 1.0), bg=(239, 239, 239, 1.0) if tag == "button" else (255, 255, 255, 1.0))
    if tag in BOLD:
        cs["weight"] = 700
    if tag == "a":
        cs["color"] = (0, 0, 238, 1.0)
    items = [(imp, 0, sp, order, n, v) for _, sp, order, decls in matched.get(id(el), []) for n, v, imp in decls]
    if el.get("style"):
        items += [(imp, 1, (0, 0, 0), 0, n, v) for n, v, imp in _decls(el["style"])]
    items.sort(key=lambda x: x[:4])
    for *_, n, v in items:
        _apply(cs, parent, n, v)
    t = cs["lh_tok"]
    if t is not None:
        if t.type == "number":
            cs["lh"] = t.value
        else:
            l = length(t, cs["size"])
            cs["lh"] = l / cs["size"] if l else cs["lh"]
    cs["sp"] = [round(l) for v in cs["spd"].values() for tk in v
                if (l := length(tk, cs["size"])) and l > 0]
    return cs

def build_styles(soup):
    matched = {}
    for order, (sel, decls) in enumerate(_rules(soup)):
        try:
            nodes = soup.select(sel)
        except Exception:
            continue
        sp = _spec(sel)
        for n in nodes:
            matched.setdefault(id(n), []).append((0, sp, order, decls))
    base = {"color": (0, 0, 0, 1.0), "size": 16, "family": "serif", "weight": 400, "lh": None}
    styles = {}
    stack = [(c, base) for c in ([soup.body] if soup.body else soup.find_all(True, recursive=False))]
    while stack:
        el, parent = stack.pop()
        if el.name in SKIP:
            continue
        cs = styles[id(el)] = _compute(el, parent, matched)
        stack += [(c, cs) for c in el.find_all(True, recursive=False)]
    return styles

def effective_bg(el, styles):
    layers, e = [], el
    while e is not None and getattr(e, "name", None):
        cs = styles.get(id(e))
        if cs:
            if cs["bg_image"]:
                return None  # gradient/image: contrast cannot be determined statically
            if cs["bg"] and cs["bg"][3] > 0:
                layers.append(cs["bg"])
                if cs["bg"][3] >= 1:
                    break
        e = e.parent
    base = (255, 255, 255, 1.0)
    for l in reversed(layers):
        base = blend(l, base)
    return base


# ---------- checks ----------
def _label(el):
    s = el.name
    if el.get("id"):
        s += "#" + el["id"]
    elif el.get("class"):
        s += "." + ".".join(el["class"][:2])
    return s

def _has_text(el):
    return any(isinstance(c, NavigableString) and not isinstance(c, Comment) and c.strip() for c in el.children)

def score(issues):
    by = {c: 100 for c in CATS}
    total = 100
    for i in issues:
        w = W[i["sev"]]
        total -= w
        by[i["cat"]] = max(0, by[i["cat"]] - w * 2.2)
    return {"total": max(0, round(total)), "by": by}

def analyze(html):
    soup = BeautifulSoup(html, "html.parser")
    styles = build_styles(soup)
    issues = []
    tok = {"colors": Counter(), "bgs": Counter(), "fonts": Counter(), "sizes": Counter(), "sp": 0, "off": 0}

    def add(sev, cat, title, detail, fix, el=None):
        issues.append({"sev": sev, "cat": cat, "title": title, "detail": detail, "fix": fix,
                       "id": None, "target": _label(el) if el is not None else ""})

    html_tag = soup.find("html")
    if not soup.title or not soup.title.get_text(strip=True):
        add("major", "Structure", "Missing page title", "No <title> element found.", "Add a short, unique <title>.")
    if not (html_tag and html_tag.get("lang")):
        add("major", "Accessibility", "Missing language attribute", "<html> has no lang.", 'Set <html lang="en"> (or the right code).')
    if not soup.find("meta", attrs={"name": "viewport"}):
        add("minor", "Layout", "No viewport meta", "Responsive scaling is not declared.",
            'Add <meta name="viewport" content="width=device-width, initial-scale=1">.')
    h1s = soup.find_all("h1")
    if not h1s:
        add("major", "Structure", "No <h1> heading", "Page has no top-level heading.", "Add one descriptive h1.")
    elif len(h1s) > 1:
        add("minor", "Structure", "Multiple <h1> headings", f"{len(h1s)} h1 elements found.", "Keep a single h1.", h1s[1])
    prev, prev_size = 0, None
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        lvl, cs = int(h.name[1]), styles.get(id(h))
        if not h.get_text(strip=True):
            add("minor", "Structure", "Empty heading", f"<{h.name}> has no text.", "Remove it or add text.", h)
        if prev and lvl > prev + 1:
            add("minor", "Structure", "Skipped heading level", f"Jumps from h{prev} to h{lvl}.", "Use consecutive heading levels.", h)
        if cs and prev_size and lvl > prev and cs["size"] > prev_size:
            add("minor", "Typography", "Heading size inverts hierarchy",
                f"A lower-level heading ({cs['size']:.0f}px) is larger than the one before ({prev_size:.0f}px).",
                "Size headings in descending order of level.", h)
        prev, prev_size = lvl, cs["size"] if cs else prev_size
    ids = Counter(e["id"] for e in soup.find_all(id=True))
    for k, n in ids.items():
        if n > 1:
            add("major", "Structure", "Duplicate id", f'id="{k}" is used {n} times.', "Make every id unique.")

    for el in soup.find_all(True):
        cs = styles.get(id(el))
        if not cs:
            continue
        tag = el.name
        for v in cs["sp"]:
            tok["sp"] += 1
            tok["off"] += 1 if (v % 4 and v > 2) else 0
        if _has_text(el):
            size, fw = cs["size"], cs["weight"]
            tok["fonts"][cs["family"]] += 1
            tok["sizes"][round(size)] += 1
            bg = effective_bg(el, styles)
            fg = cs["color"]
            if bg:
                if fg[3] < 1:
                    fg = blend(fg, bg)
                tok["colors"][to_hex(fg)] += 1
                tok["bgs"][to_hex(bg)] += 1
                cr = contrast(fg, bg)
                need = 3 if (size >= 24 or (size >= 18.66 and fw >= 700)) else 4.5
                if cr < need:
                    add("critical" if cr < need * .67 else "major", "Accessibility", "Low colour contrast",
                        f"{to_hex(fg)} on {to_hex(bg)} = {cr:.2f}:1 (needs {need}:1) at {size:.0f}px.",
                        f"Darken the text or lighten the background until the ratio reaches {need}:1.", el)
            if size < 12:
                add("major", "Typography", "Text too small", f"{size:.0f}px is below the 12px floor.",
                    "Use at least 14px for body text; 12px only for captions.", el)
            if cs["lh"] and cs["lh"] < 1.3 and len(el.get_text(strip=True)) > 60:
                add("minor", "Typography", "Tight line height", f"Line height is {cs['lh']:.2f}× on multi-line text.",
                    "Set line-height between 1.4 and 1.6 for body copy.", el)
        if tag in ("a", "button") or tag in FORM or el.get("role") == "button":
            if tag == "input" and el.get("type") == "hidden":
                continue
            name = (el.get_text(strip=True) or el.get("aria-label") or el.get("title")
                    or (tag == "input" and (el.get("value") or el.get("placeholder")))
                    or el.get("aria-labelledby") or el.find_parent("label")
                    or (el.get("id") and soup.find("label", attrs={"for": el["id"]}))
                    or el.find("img", alt=lambda a: a))
            if not name and tag not in ("select", "textarea"):
                add("critical", "Accessibility", "Control has no accessible name",
                    "Button or link has no text, aria-label or title.", "Add visible text or an aria-label.", el)
            if tag == "a" and VAGUE.match(el.get_text(strip=True)):
                add("minor", "Accessibility", "Vague link text", f"“{el.get_text(strip=True)}” says nothing out of context.",
                    "Describe the destination, e.g. “View roast schedule”.", el)
            if tag == "a" and not el.get("href"):
                add("minor", "Accessibility", "Link without href", "Anchor is not keyboard focusable.", "Add an href or use a <button>.", el)
            if cs["outline_none"] and tag in ("a", "button", "input"):
                add("major", "Accessibility", "Focus outline removed",
                    "CSS sets outline to none.", "Provide a visible :focus-visible style instead.", el)
        if tag in ("input", "select", "textarea") and el.get("type") not in ("hidden", "submit", "button"):
            lab = el.get("aria-label") or el.get("aria-labelledby") or el.find_parent("label") or (
                el.get("id") and soup.find("label", attrs={"for": el["id"]}))
            if not lab:
                add("major", "Accessibility", "Form field has no label",
                    "Placeholder text alone disappears while typing and is not a label.", "Add a <label for> or aria-label.", el)
        if tag == "img":
            if el.get("alt") is None:
                add("critical", "Accessibility", "Image missing alt text", "No alt attribute.",
                    'Describe the image, or use alt="" if purely decorative.', el)
            if not (el.get("width") or el.get("height")) and "width" not in (el.get("style") or ""):
                add("minor", "Layout", "Image has no reserved size", "Missing width/height can cause layout shift.",
                    "Set width and height attributes.", el)

    fams, sizes = list(tok["fonts"]), list(tok["sizes"])
    if len(fams) > 3:
        add("minor", "Consistency", "Too many font families", f"{len(fams)} families: {', '.join(fams)}.", "Limit to one or two typefaces.")
    if len(sizes) > 7:
        add("minor", "Consistency", "Type scale is fragmented", f"{len(sizes)} distinct sizes in use.", "Adopt a modular scale (14/16/20/24/32/40).")
    if tok["sp"] > 8 and tok["off"] / tok["sp"] > .25:
        add("minor", "Consistency", "Spacing is off a 4px grid",
            f"{round(tok['off'] / tok['sp'] * 100)}% of margin/padding values are not multiples of 4.", "Use a spacing scale: 4, 8, 12, 16, 24, 32.")
    if len(tok["colors"]) > 8:
        add("minor", "Consistency", "Many text colours", f"{len(tok['colors'])} distinct text colours.", "Reduce to a small palette of semantic tokens.")

    tokens = {k: (dict(v.most_common(12)) if isinstance(v, Counter) else v) for k, v in tok.items()}
    return {"score": score(issues), "issues": issues, "tokens": tokens,
            "stats": {"elements": len(styles), "issues": len(issues)},
            "title": soup.title.get_text(strip=True) if soup.title else ""}
