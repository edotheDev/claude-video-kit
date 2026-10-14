#!/usr/bin/env python3
"""Download Wikimedia Commons files with their licence, author and credit recorded in ./stills/sources.json
(set STILLS_OUT to change the folder):  python commons_get.py name="File:..." ...
Check every licence it prints before use. CC BY needs a credit line; skip CC BY-SA for anything commercial."""
import json, os, sys, time, urllib.parse, urllib.request
UA = os.environ.get('MOTION_UA', 'BrandMotionKit/1.0 (+https://github.com/edotheDev/claude-video-kit)')
OUT = os.environ.get('STILLS_OUT', 'stills'); os.makedirs(OUT, exist_ok=True)
sp = os.path.join(OUT, 'sources.json'); rec = json.load(open(sp)) if os.path.exists(sp) else {}
for arg in sys.argv[1:]:
    n, t = arg.split('=', 1)
    q = 'https://commons.wikimedia.org/w/api.php?action=query&prop=imageinfo&iiprop=url|extmetadata|size|mime&iiurlwidth=3000&format=json&titles=' + urllib.parse.quote(t)
    d = json.load(urllib.request.urlopen(urllib.request.Request(q, headers={'User-Agent': UA})))
    ii = list(d['query']['pages'].values())[0]['imageinfo'][0]; m = ii['extmetadata']
    url = ii['url'] if ii['mime'] in ('image/jpeg', 'image/png') and ii['width'] <= 6000 else ii.get('thumburl', ii['url'])
    ext = '.png' if url.lower().endswith('.png') else '.jpg'
    time.sleep(1)
    data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA})).read()
    open(os.path.join(OUT, n + ext), 'wb').write(data)
    rec[n] = dict(file=n + ext, commons_title=t, page=ii['descriptionurl'], licence=m.get('LicenseShortName', {}).get('value'),
                  artist=m.get('Artist', {}).get('value', '')[:200], credit=m.get('Credit', {}).get('value', '')[:200], size=(ii['width'], ii['height']))
    print(n, rec[n]["licence"], ii["width"], ii["height"], len(data), flush=True); json.dump(rec, open(sp, "w"), indent=1); time.sleep(4)
json.dump(rec, open(sp, 'w'), indent=1)
