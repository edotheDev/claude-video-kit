import re, sys, json, subprocess, html
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
def get(url):
    r = subprocess.run(["curl", "-sL", "-A", UA, "-H", "Accept: text/html", url], capture_output=True)
    return r.stdout.decode("utf-8", "ignore")
items = {}
for path in sys.argv[1:]:
    for page in (1, 2):
        url = f"https://mixkit.co/free-stock-music/{path}/" + (f"?page={page}" if page > 1 else "")
        h = get(url)
        blocks = h.split('data-test-id="audio-player"')[1:]
        for b in blocks:
            m = re.search(r'music/(\d+)/\1\.mp3', b)
            if not m: continue
            i = int(m.group(1))
            t = re.search(r'item-grid-card__title">\s*(.*?)\s*</h2>', b, re.S)
            a = re.search(r'music-preview__author">\s*by\s*(.*?)\s*</p>', b, re.S)
            d = re.search(r'data-test-id="duration">\s*([\d:]+)', b)
            tags = re.findall(r'data-global--meta-links-target="link" href="[^"]*">([^<]+)</a>', b)
            it = items.setdefault(i, {"id": i, "title": html.unescape(t.group(1)) if t else "?", "by": html.unescape(a.group(1)) if a else "?",
                                     "dur": d.group(1) if d else "?", "tags": [html.unescape(x) for x in tags], "src": set()})
            it["src"].add(path)
for i in sorted(items):
    it = items[i]
    print(f'{i:5d} | {it["title"][:34]:34s} | {it["by"][:22]:22s} | {it["dur"]:5s} | {", ".join(it["tags"])[:110]} | {",".join(sorted(it["src"]))}')
