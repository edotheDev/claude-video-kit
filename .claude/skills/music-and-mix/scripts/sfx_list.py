import re, sys, subprocess, html
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
for cat in sys.argv[1:]:
    h = subprocess.run(["curl", "-sL", "-A", UA, f"https://mixkit.co/free-sound-effects/{cat}/"], capture_output=True).stdout.decode("utf-8", "ignore")
    blocks = h.split('data-test-id="audio-player"')[1:]
    print("==", cat, len(blocks))
    for b in blocks:
        m = re.search(r'sfx/(\d+)/\1-preview\.mp3', b)
        if not m:
            continue
        t = re.search(r'item-grid-card__title">\s*(.*?)\s*</', b, re.S)
        d = re.search(r'data-test-id="duration">\s*([\d:]+)', b)
        print(f"  {m.group(1):6s} {html.unescape(t.group(1)).strip()[:50] if t else '?':50s} {d.group(1) if d else '?'}")
