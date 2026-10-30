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

def save(name, fields, rows, minimum):
    rows = [[None if isinstance(v, str) and v.strip().upper() == 'NULL' else v for v in r] for r in rows]   # the word "NULL" means empty
    if len(rows) < minimum:
        print(f'{name}: only {len(rows)} rows, keeping the old copy'); return
    out = {'at': int(time.time() * 1000), 'fields': fields, 'rows': rows}
    with open(f'data/{name}.json', 'w') as fh: json.dump(out, fh, separators=(',', ':'))
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
try:
    f = from_any([(VACANT, 1)], {})
    rows = [[r6(x['geometry']['x']), r6(x['geometry']['y']), attr(x['attributes'], 'Address'),
             attr(x['attributes'], 'NoticeNum'), attr(x['attributes'], 'DateNotice')]
            for x in f if x.get('geometry') and x['geometry'].get('x') is not None]
    save('vacants', ['Address', 'NoticeNum', 'DateNotice'], rows, 500)
except Exception as e: failed.append('vacants'); print('vacants failed:', e)

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
