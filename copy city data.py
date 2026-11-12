# Copies Baltimore City box areas, hydrants, firehouses and vacant notices into data/ (run by the "Copy city data" GitHub job).
import json, time, urllib.parse, urllib.request, sys, os

CITY = os.environ.get('CITY_URL', 'https://egisdata.baltimorecity.gov/egis/rest/services/911/BaltCity_911_RapidDeploy_layers/MapServer')
GIS = os.environ.get('GIS_URL', 'https://gisdata.baltimorecity.gov/egis/rest/services/CAD911/')
VACANT = os.environ.get('VACANT_URL', 'https://egisdata.baltimorecity.gov/egis/rest/services/Housing/DHCD_Open_Baltimore_Datasets/FeatureServer')
UA = {'User-Agent': 'BCFD-Box-Info data copy (github.com/josephheinze/BCFD-Box-Area-)'}

# The city's 911 servers have been hanging instead of answering: give each request 25 s and one retry, so a dead
# server is skipped in about a minute and the job moves on to the other sources.
def get(url, tries=2):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            print('  retry', i + 1, url[:120], e)
            time.sleep(5 * (i + 1))
    raise RuntimeError('no answer: ' + url[:120])

def layer_id(svc):
    try: return (get(svc + '?f=json').get('layers') or [{'id': 0}])[0]['id']
    except Exception: return 0

def query_all(base, layer, extra):
    feats, offset = [], 0
    while True:
        q = dict(where='1=1', outFields='*', returnGeometry='true', outSR='4326', f='json', resultOffset=str(offset))
        q.update(extra)
        d = get(f'{base}/{layer}/query?' + urllib.parse.urlencode(q))
        if 'error' in d: raise RuntimeError(str(d['error']))
        got = d.get('features') or []
        feats += got
        offset += len(got)
        if not d.get('exceededTransferLimit') or not got or offset > 60000: break
    return feats

def from_any(sources, extra):
    last = None
    for base, layer in sources:
        try:
            f = query_all(base, layer, extra)
            if f: return f
        except Exception as e:
            last = e; print('  source failed:', base, e)
    raise RuntimeError(f'all sources failed: {last}')

def attr(a, name):
    if name in a: return a[name]
    for k, v in a.items():
        if k.lower() == name.lower() or k.lower().endswith('.' + name.lower()): return v
    return None

status_ok = {}
notes = {}
def save(name, fields, rows, minimum, extra=None):
    rows = [[None if isinstance(v, str) and v.strip().upper() == 'NULL' else v for v in r] for r in rows]   # the word "NULL" means empty
    if len(rows) < minimum:
        print(f'{name}: only {len(rows)} rows, keeping the old copy'); return
    status_ok[name] = True
    path = f'data/{name}.json'
    try:
        old = json.load(open(path))
        if old.get('fields') == fields and old.get('rows') == json.loads(json.dumps(rows)) and \
           all(old.get(k) == v for k, v in (extra or {}).items()):
            print(f'{name}: unchanged ({len(rows)} rows)'); return      # same data: don't rewrite the big file
    except Exception: pass
    out = {'at': int(time.time() * 1000), 'fields': fields, 'rows': rows}
    out.update(extra or {})
    with open(path, 'w') as fh: json.dump(out, fh, separators=(',', ':'))
    print(f'{name}: saved {len(rows)} rows')

r6 = lambda v: round(v, 6)
failed = []

# box areas
try:
    f = from_any([(CITY, 12), (GIS + 'Fire_RDs/FeatureServer', layer_id(GIS + 'Fire_RDs/FeatureServer'))],
                 {'maxAllowableOffset': '0.00002', 'geometryPrecision': '6'})
    rows = [[attr(x['attributes'], 'Area'), attr(x['attributes'], 'MapBook'),
             [[[r6(c[0]), r6(c[1])] for c in ring] for ring in x['geometry']['rings']]]
            for x in f if x.get('geometry') and x['geometry'].get('rings') and attr(x['attributes'], 'Area')]
    save('boxes', ['Area', 'MapBook', 'rings'], rows, 50)
except Exception as e: failed.append('boxes'); print('boxes failed:', e)

# hydrants
HF = ['HYDRANT_ID', 'CAD_ADDR', 'STREET_NUM', 'STREET_NAM', 'CROSS_STRE', 'CORNER_POS', 'LOCATION_D', 'LOCATION_1',
      'SRC_MAIN_S', 'FEAT_STATU', 'CONDITION', 'DATEABANDO', 'RESP_AGENC', 'SERVICE']
DPW = os.environ.get('DPW_URL', 'https://dpwview.baltimorecity.gov/agsprd/rest/services/Hydrants/MapServer')
def dpw_hydrants():
    # DPW's own hydrant map: one layer per status (0 in service, 1 pending inspection, 2 repairs needed, 3 out of service, 4 no inspection)
    flags = {2: 'Repairs needed (DPW)', 3: 'OUT OF SERVICE (DPW)'}
    rows = []
    for layer in range(5):
        for x in query_all(DPW, layer, {}):
            a, g = x['attributes'], x.get('geometry') or {}
            if g.get('x') is None: continue
            vals = {'HYDRANT_ID': attr(a, 'HYDRANT_ID'), 'CAD_ADDR': attr(a, 'CAD_ADDR'), 'STREET_NUM': attr(a, 'STREET_NUM'),
                    'STREET_NAM': attr(a, 'STREET_NAME'), 'CROSS_STRE': attr(a, 'CROSS_STREET'), 'CORNER_POS': attr(a, 'CORNER_POSITION'),
                    'LOCATION_D': attr(a, 'LOCATION_DESCRIPTION'), 'LOCATION_1': attr(a, 'LOCATION_DESCRIPTION2'), 'SRC_MAIN_S': attr(a, 'SRC_MAIN_SIZE'),
                    'FEAT_STATU': attr(a, 'FEAT_STATUS'), 'CONDITION': attr(a, 'CONDITION'), 'DATEABANDO': attr(a, 'DATEABANDONED'),
                    'RESP_AGENC': attr(a, 'RESP_AGENCY'), 'SERVICE': flags.get(layer) or attr(a, 'SERVICE')}
            rows.append([r6(g['x']), r6(g['y'])] + [vals[k] for k in HF])
    return rows
try:
    try:
        f = from_any([(CITY, 3), (GIS + 'Hydrants_City/FeatureServer', layer_id(GIS + 'Hydrants_City/FeatureServer'))], {})
        rows = [[r6(x['geometry']['x']), r6(x['geometry']['y'])] + [attr(x['attributes'], k) for k in HF]
                for x in f if x.get('geometry') and x['geometry'].get('x') is not None]
    except Exception as e1:
        print('city hydrant layers failed, using DPW hydrant map:', e1)
        rows = dpw_hydrants()
    save('hydrants', HF, rows, 1000)
except Exception as e: failed.append('hydrants'); print('hydrants failed:', e)

# firehouses
try:
    f = from_any([(CITY, 0), (GIS + 'fire_station/FeatureServer', layer_id(GIS + 'fire_station/FeatureServer'))], {})
    rows = [[r6(x['geometry']['x']), r6(x['geometry']['y']), attr(x['attributes'], 'NAME'), attr(x['attributes'], 'ADDRESS')]
            for x in f if x.get('geometry') and x['geometry'].get('x') is not None]
    save('stations', ['NAME', 'ADDRESS'], rows, 20)
except Exception as e: failed.append('stations'); print('stations failed:', e)

# vacant notices
# First the city's housing (DHCD) server, as always. If it fails (broken since the late-October city outage), the same
# notices from the city's Open Baltimore data site: the service its listing reads from, then its stored download files.
# A copy made that way is marked source "open-data" with the date of the newest notice in it; the app always asks the
# city first for those, shows the copy only when the city fails, and labels how old it is.
OB_ITEM = os.environ.get('VACANT_ITEM', '691d65a5f85640e6aaa46930bd9dc102')   # Open Baltimore: "Vacant Building Notices"
BALT = (-76.75, 39.18, -76.50, 39.40)

def to_ms(v):
    if v is None or v == '': return None
    if isinstance(v, (int, float)): return int(v if v > 1e11 else v * 1000)
    from datetime import datetime, timezone
    t = str(v).strip().replace('/', '-').replace('T', ' ').rstrip('Z')
    t = t.split('+')[0].split('.')[0].strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%m-%d-%Y %H:%M:%S', '%m-%d-%Y'):
        try: return int(datetime.strptime(t, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError: pass
    return None

def pick(keys, *tests):
    for test in tests:
        for k in keys:
            if test(k.lower().replace('_', '').replace(' ', '')): return k
    return None

def vacant_rows(points):
    # points: [(x, y, attributes)]. Only accepted when it really is the vacant notice list: address and notice fields,
    # points inside Baltimore.
    keys = set()
    for _, _, a in points[:50]: keys.update(a.keys())
    ka = pick(keys, lambda k: k == 'address', lambda k: k in ('fulladdr', 'fulladdress', 'propertyaddress'), lambda k: 'address' in k)
    kn = pick(keys, lambda k: k == 'noticenum', lambda k: 'notice' in k and ('num' in k or k.endswith('no') or 'id' in k))
    kd = pick(keys, lambda k: k == 'datenotice', lambda k: 'notice' in k and 'date' in k, lambda k: 'date' in k)
    if not ka or not kn: raise RuntimeError(f'not the vacant notice list (fields: {sorted(keys)[:20]})')
    rows = [[r6(x), r6(y), a.get(ka), a.get(kn), to_ms(a.get(kd)) if kd else None] for x, y, a in points
            if x is not None and y is not None and BALT[0] < x < BALT[2] and BALT[1] < y < BALT[3]]
    return rows

def open_baltimore_vacants():
    import re
    tried = []
    try:   # 1. the service Open Baltimore's listing reads from, when it's not the broken city server
        item = get(f'https://www.arcgis.com/sharing/rest/content/items/{OB_ITEM}?f=json', tries=1)
        url = (item.get('url') or '').rstrip('/')
        tried.append('listing service: ' + (url or 'none'))
        if url and 'egisdata.baltimorecity.gov' not in url:
            m = re.search(r'/(\d+)$', url)
            base, layers = (url[:m.start()], [int(m.group(1))]) if m else (url, [l['id'] for l in (get(url + '?f=json', tries=1).get('layers') or [])])
            for L in layers:
                try:
                    rows = vacant_rows([(x['geometry'].get('x'), x['geometry'].get('y'), x['attributes'])
                                        for x in query_all(base, L, {}) if x.get('geometry')])
                    if len(rows) >= 500: return rows, f'{base}/{L}'
                    tried.append(f'layer {L}: only {len(rows)}')
                except Exception as e: tried.append(f'layer {L}: {e}')
    except Exception as e: tried.append(f'listing: {e}')
    for L in (1, 0, None):   # 2. Open Baltimore's stored download files (GeoJSON)
        sfx = '' if L is None else f'_{L}'
        for url in ([f'https://opendata.arcgis.com/api/v3/datasets/{OB_ITEM}{sfx}/downloads/data?format=geojson&spatialRefId=4326&where=1%3D1',
                     f'https://hub.arcgis.com/api/v3/datasets/{OB_ITEM}{sfx}/downloads/data?format=geojson&spatialRefId=4326&where=1%3D1'] +
                    ([f'https://data.baltimorecity.gov/api/download/v1/items/{OB_ITEM}/geojson?layers={L}'] if L is not None else [])):
            try:
                d = get(url, tries=1)
                pts = [((x.get('geometry') or {}).get('coordinates') or [None, None])[:2] + [x.get('properties') or {}]
                       for x in d.get('features') or [] if (x.get('geometry') or {}).get('type') == 'Point']
                rows = vacant_rows(pts)
                if len(rows) >= 500: return rows, url.split('?')[0]
                tried.append(f'{url[:90]}: only {len(rows)}')
            except Exception as e: tried.append(f'{url[:90]}: {str(e)[:120]}')
    raise RuntimeError('Open Baltimore failed too: ' + ' | '.join(tried))

try:
    try:
        f = from_any([(VACANT, 1)], {})
        rows = [[r6(x['geometry']['x']), r6(x['geometry']['y']), attr(x['attributes'], 'Address'),
                 attr(x['attributes'], 'NoticeNum'), attr(x['attributes'], 'DateNotice')]
                for x in f if x.get('geometry') and x['geometry'].get('x') is not None]
        save('vacants', ['Address', 'NoticeNum', 'DateNotice'], rows, 500, {'source': 'city'})
        if status_ok.get('vacants'): notes['vacants'] = 'city housing server'
    except Exception as e1:
        print('city vacant server failed, trying Open Baltimore:', e1)
        rows, src = open_baltimore_vacants()
        newest = max([r[4] for r in rows if isinstance(r[4], (int, float))] or [0]) or None
        save('vacants', ['Address', 'NoticeNum', 'DateNotice'], rows, 500, {'source': 'open-data', 'from': src, 'newest': newest})
        status_ok.pop('vacants', None)       # not "current": the app keeps asking the city first
        notes['vacants'] = f'city failed; Open Baltimore copy from {src}, {len(rows)} notices, newest ' + \
            (time.strftime('%Y-%m-%d', time.gmtime(newest / 1000)) if newest else 'unknown')
        failed.append('vacants (city)')
except Exception as e: failed.append('vacants'); notes['vacants'] = str(e)[:600]; print('vacants failed:', e)

# a tiny file saying when the city was last checked and which copies are current (the app reads this for freshness)
with open('data/status.json', 'w') as fh:
    json.dump({'checked': int(time.time() * 1000), 'ok': status_ok, 'failed': failed, 'notes': notes}, fh, separators=(',', ':'))
print('done; failed:', failed or 'none')

# Save the copies to the site right here (pulling first, so an upload made while this ran can't block the save).
import subprocess
def git(*a, check=True):
    print('$ git', ' '.join(a)); return subprocess.run(['git', *a], check=check)
if os.path.isdir('.git') and not os.environ.get('NO_GIT'):
    git('config', 'user.name', 'city-data-bot'); git('config', 'user.email', 'city-data-bot@users.noreply.github.com')
    git('add', 'data')
    if subprocess.run(['git', 'diff', '--cached', '--quiet']).returncode != 0:
        git('commit', '-m', 'Update city data copy')
        for attempt in range(4):
            git('pull', '--rebase', '--autostash', 'origin', 'main', check=False)
            if git('push', 'origin', 'HEAD:main', check=False).returncode == 0:
                print('saved to the site'); break
            time.sleep(5)
        else:
            sys.exit('could not save to the site')
    else:
        print('nothing new to save')
