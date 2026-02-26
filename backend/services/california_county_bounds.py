"""California county coordinate bounds and lookup helpers."""

import math

from backend.services.california_counties import CALIFORNIA_COUNTIES


COUNTY_BOUNDS_BY_SLUG = {
    'alameda': {'min_lat': 37.454438, 'max_lat': 37.905025, 'min_lng': -122.331551, 'max_lng': -121.469275},
    'alpine': {'min_lat': 38.32688, 'max_lat': 38.933324, 'min_lng': -120.072566, 'max_lng': -119.542367},
    'amador': {'min_lat': 38.217951, 'max_lat': 38.709029, 'min_lng': -121.027507, 'max_lng': -120.072382},
    'butte': {'min_lat': 39.295621, 'max_lat': 40.151905, 'min_lng': -122.069431, 'max_lng': -121.076695},
    'calaveras': {'min_lat': 37.831422, 'max_lat': 38.509869, 'min_lng': -120.995497, 'max_lng': -120.019951},
    'colusa': {'min_lat': 38.923897, 'max_lat': 39.414499, 'min_lng': -122.78509, 'max_lng': -121.795366},
    'contra-costa': {'min_lat': 37.718629, 'max_lat': 38.0996, 'min_lng': -122.428857, 'max_lng': -121.536595},
    'del-norte': {'min_lat': 41.380776, 'max_lat': 42.000854, 'min_lng': -124.25517, 'max_lng': -123.517907},
    'el-dorado': {'min_lat': 38.502349, 'max_lat': 39.067489, 'min_lng': -121.141009, 'max_lng': -119.877898},
    'fresno': {'min_lat': 35.907186, 'max_lat': 37.585737, 'min_lng': -120.918731, 'max_lng': -118.360586},
    'glenn': {'min_lat': 39.382973, 'max_lat': 39.800561, 'min_lng': -122.938413, 'max_lng': -121.856532},
    'humboldt': {'min_lat': 40.001275, 'max_lat': 41.465844, 'min_lng': -124.408719, 'max_lng': -123.406082},
    'imperial': {'min_lat': 32.618592, 'max_lat': 33.433708, 'min_lng': -116.10618, 'max_lng': -114.462929},
    'inyo': {'min_lat': 35.786762, 'max_lat': 37.464929, 'min_lng': -118.790031, 'max_lng': -115.648357},
    'kern': {'min_lat': 34.790629, 'max_lat': 35.798202, 'min_lng': -120.194146, 'max_lng': -117.616195},
    'kings': {'min_lat': 35.78878, 'max_lat': 36.488835, 'min_lng': -120.315068, 'max_lng': -119.474607},
    'lake': {'min_lat': 38.667506, 'max_lat': 39.5814, 'min_lng': -123.094213, 'max_lng': -122.340172},
    'lassen': {'min_lat': 39.707658, 'max_lat': 41.184514, 'min_lng': -121.332338, 'max_lng': -119.995705},
    'los-angeles': {'min_lat': 32.803855, 'max_lat': 34.823251, 'min_lng': -118.94485, 'max_lng': -117.646374},
    'madera': {'min_lat': 36.769611, 'max_lat': 37.777986, 'min_lng': -120.545536, 'max_lng': -119.022363},
    'marin': {'min_lat': 37.819924, 'max_lat': 38.321227, 'min_lng': -123.017794, 'max_lng': -122.418804},
    'mariposa': {'min_lat': 37.183109, 'max_lat': 37.902922, 'min_lng': -120.39377, 'max_lng': -119.308995},
    'mendocino': {'min_lat': 38.763559, 'max_lat': 40.002123, 'min_lng': -124.023057, 'max_lng': -122.821388},
    'merced': {'min_lat': 36.740381, 'max_lat': 37.633364, 'min_lng': -121.245989, 'max_lng': -120.054096},
    'modoc': {'min_lat': 41.183484, 'max_lat': 41.997613, 'min_lng': -121.457213, 'max_lng': -119.998287},
    'mono': {'min_lat': 37.462588, 'max_lat': 38.713212, 'min_lng': -119.651509, 'max_lng': -117.832726},
    'monterey': {'min_lat': 35.788977, 'max_lat': 36.915304, 'min_lng': -121.975946, 'max_lng': -120.213979},
    'napa': {'min_lat': 38.155017, 'max_lat': 38.864245, 'min_lng': -122.646421, 'max_lng': -122.061379},
    'nevada': {'min_lat': 39.006443, 'max_lat': 39.526113, 'min_lng': -121.279784, 'max_lng': -120.003773},
    'orange': {'min_lat': 33.386416, 'max_lat': 33.946873, 'min_lng': -118.119423, 'max_lng': -117.412987},
    'placer': {'min_lat': 38.711502, 'max_lat': 39.316496, 'min_lng': -121.48444, 'max_lng': -120.002461},
    'plumas': {'min_lat': 39.597264, 'max_lat': 40.449715, 'min_lng': -121.49788, 'max_lng': -120.099339},
    'riverside': {'min_lat': 33.425888, 'max_lat': 34.079791, 'min_lng': -117.675053, 'max_lng': -114.434949},
    'sacramento': {'min_lat': 38.024592, 'max_lat': 38.736401, 'min_lng': -121.834047, 'max_lng': -121.027084},
    'san-benito': {'min_lat': 36.198569, 'max_lat': 36.988944, 'min_lng': -121.642797, 'max_lng': -120.596562},
    'san-bernardino': {'min_lat': 33.870831, 'max_lat': 35.809211, 'min_lng': -117.802539, 'max_lng': -114.131211},
    'san-diego': {'min_lat': 32.534286, 'max_lat': 33.505025, 'min_lng': -117.595944, 'max_lng': -116.08109},
    'san-francisco': {'min_lat': 37.708133, 'max_lat': 37.831073, 'min_lng': -122.514595, 'max_lng': -122.356965},
    'san-joaquin': {'min_lat': 37.481783, 'max_lat': 38.300252, 'min_lng': -121.584074, 'max_lng': -120.920665},
    'san-luis-obispo': {'min_lat': 34.897475, 'max_lat': 35.79519, 'min_lng': -121.347884, 'max_lng': -119.472719},
    'san-mateo': {'min_lat': 37.107335, 'max_lat': 37.70828, 'min_lng': -122.519925, 'max_lng': -122.115877},
    'santa-barbara': {'min_lat': 33.465961, 'max_lat': 35.114335, 'min_lng': -120.671649, 'max_lng': -119.027973},
    'santa-clara': {'min_lat': 36.896252, 'max_lat': 37.484637, 'min_lng': -122.200706, 'max_lng': -121.208228},
    'santa-cruz': {'min_lat': 36.851426, 'max_lat': 37.286055, 'min_lng': -122.317682, 'max_lng': -121.581154},
    'shasta': {'min_lat': 40.285375, 'max_lat': 41.184861, 'min_lng': -123.068789, 'max_lng': -121.319972},
    'sierra': {'min_lat': 39.391558, 'max_lat': 39.77606, 'min_lng': -121.057845, 'max_lng': -120.00082},
    'siskiyou': {'min_lat': 40.992433, 'max_lat': 42.009517, 'min_lng': -123.719174, 'max_lng': -121.446346},
    'solano': {'min_lat': 38.042586, 'max_lat': 38.53905, 'min_lng': -122.40699, 'max_lng': -121.593273},
    'sonoma': {'min_lat': 38.110596, 'max_lat': 38.852916, 'min_lng': -123.533665, 'max_lng': -122.350391},
    'stanislaus': {'min_lat': 37.134774, 'max_lat': 38.077421, 'min_lng': -121.486775, 'max_lng': -120.387329},
    'sutter': {'min_lat': 38.734598, 'max_lat': 39.305668, 'min_lng': -121.948177, 'max_lng': -121.414399},
    'tehama': {'min_lat': 39.797499, 'max_lat': 40.453133, 'min_lng': -123.066009, 'max_lng': -121.342264},
    'trinity': {'min_lat': 39.977015, 'max_lat': 41.367922, 'min_lng': -123.623891, 'max_lng': -122.446217},
    'tulare': {'min_lat': 35.789161, 'max_lat': 36.744773, 'min_lng': -119.573194, 'max_lng': -117.981043},
    'tuolumne': {'min_lat': 37.633704, 'max_lat': 38.433521, 'min_lng': -120.652673, 'max_lng': -119.20128},
    'ventura': {'min_lat': 33.214691, 'max_lat': 34.901274, 'min_lng': -119.573521, 'max_lng': -118.632495},
    'yolo': {'min_lat': 38.313089, 'max_lat': 38.925913, 'min_lng': -122.422048, 'max_lng': -121.501017},
    'yuba': {'min_lat': 38.924363, 'max_lat': 39.639459, 'min_lng': -121.636368, 'max_lng': -121.009477},
}

COUNTY_CENTROIDS_BY_SLUG = {
    'alameda': {'lat': 37.667566, 'lng': -122.039171},
    'alpine': {'lat': 38.591468, 'lng': -119.735044},
    'amador': {'lat': 38.480012, 'lng': -120.587510},
    'butte': {'lat': 39.693957, 'lng': -121.588735},
    'calaveras': {'lat': 38.197901, 'lng': -120.498900},
    'colusa': {'lat': 39.156667, 'lng': -122.248365},
    'contra-costa': {'lat': 37.971468, 'lng': -121.893023},
    'del-norte': {'lat': 41.711863, 'lng': -123.836034},
    'el-dorado': {'lat': 38.766214, 'lng': -120.599732},
    'fresno': {'lat': 36.868529, 'lng': -119.631064},
    'glenn': {'lat': 39.607446, 'lng': -122.249755},
    'humboldt': {'lat': 40.846215, 'lng': -123.858526},
    'imperial': {'lat': 33.088674, 'lng': -114.965094},
    'inyo': {'lat': 36.624373, 'lng': -118.074605},
    'kern': {'lat': 35.363513, 'lng': -118.892292},
    'kings': {'lat': 36.127443, 'lng': -119.829618},
    'lake': {'lat': 39.100986, 'lng': -122.738589},
    'lassen': {'lat': 40.293759, 'lng': -120.553107},
    'los-angeles': {'lat': 33.692993, 'lng': -118.356734},
    'madera': {'lat': 37.134593, 'lng': -119.782728},
    'marin': {'lat': 38.031683, 'lng': -122.682724},
    'mariposa': {'lat': 37.683161, 'lng': -119.948965},
    'mendocino': {'lat': 39.328100, 'lng': -123.357559},
    'merced': {'lat': 37.152530, 'lng': -120.718794},
    'modoc': {'lat': 41.615126, 'lng': -120.791790},
    'mono': {'lat': 38.009911, 'lng': -119.207860},
    'monterey': {'lat': 36.361668, 'lng': -121.257939},
    'napa': {'lat': 38.482501, 'lng': -122.326196},
    'nevada': {'lat': 39.292161, 'lng': -120.918681},
    'orange': {'lat': 33.693838, 'lng': -117.833506},
    'placer': {'lat': 39.002577, 'lng': -120.880773},
    'plumas': {'lat': 40.011554, 'lng': -120.929463},
    'riverside': {'lat': 33.758486, 'lng': -115.860819},
    'sacramento': {'lat': 38.309722, 'lng': -121.509197},
    'san-benito': {'lat': 36.584563, 'lng': -121.151200},
    'san-bernardino': {'lat': 34.608586, 'lng': -115.832491},
    'san-diego': {'lat': 32.929779, 'lng': -117.057722},
    'san-francisco': {'lat': 37.766713, 'lng': -122.415318},
    'san-joaquin': {'lat': 37.969943, 'lng': -121.360578},
    'san-luis-obispo': {'lat': 35.254730, 'lng': -120.426680},
    'san-mateo': {'lat': 37.438404, 'lng': -122.290943},
    'santa-barbara': {'lat': 34.433738, 'lng': -120.026968},
    'santa-clara': {'lat': 37.208007, 'lng': -121.708822},
    'santa-cruz': {'lat': 37.020430, 'lng': -121.895548},
    'shasta': {'lat': 40.594909, 'lng': -122.417316},
    'sierra': {'lat': 39.581232, 'lng': -120.748432},
    'siskiyou': {'lat': 41.424580, 'lng': -123.023328},
    'solano': {'lat': 38.253427, 'lng': -121.966218},
    'sonoma': {'lat': 38.465487, 'lng': -122.838482},
    'stanislaus': {'lat': 37.523796, 'lng': -121.122361},
    'sutter': {'lat': 39.011835, 'lng': -121.703725},
    'tehama': {'lat': 40.194340, 'lng': -122.226564},
    'trinity': {'lat': 40.847329, 'lng': -122.975110},
    'tulare': {'lat': 36.319294, 'lng': -118.439070},
    'tuolumne': {'lat': 37.972793, 'lng': -119.934533},
    'ventura': {'lat': 34.111661, 'lng': -119.264315},
    'yolo': {'lat': 38.658853, 'lng': -121.846385},
    'yuba': {'lat': 39.280450, 'lng': -121.352136},
}

COUNTY_NAME_BY_SLUG = {item['slug']: item['name'] for item in CALIFORNIA_COUNTIES}


def _normalize_slug(value):
    text = str(value or '').strip().lower().replace('_', '-').replace(' ', '-')
    while '--' in text:
        text = text.replace('--', '-')
    return text.strip('-')


def county_name_for_slug(county_slug):
    return COUNTY_NAME_BY_SLUG.get(_normalize_slug(county_slug), '')


def get_county_bounds(county_slug):
    return COUNTY_BOUNDS_BY_SLUG.get(_normalize_slug(county_slug))


def is_point_within_county_bounds(lat, lng, county_slug, margin=0.0):
    bounds = get_county_bounds(county_slug)
    if not bounds:
        return False
    try:
        lat_value = float(lat)
        lng_value = float(lng)
        margin_value = max(0.0, float(margin or 0.0))
    except (TypeError, ValueError):
        return False
    return (
        bounds['min_lat'] - margin_value <= lat_value <= bounds['max_lat'] + margin_value
        and bounds['min_lng'] - margin_value <= lng_value <= bounds['max_lng'] + margin_value
    )


def counties_for_point(lat, lng, margin=0.0):
    try:
        lat_value = float(lat)
        lng_value = float(lng)
        margin_value = max(0.0, float(margin or 0.0))
    except (TypeError, ValueError):
        return []

    matches = []
    for slug, bounds in COUNTY_BOUNDS_BY_SLUG.items():
        if (
            bounds['min_lat'] - margin_value <= lat_value <= bounds['max_lat'] + margin_value
            and bounds['min_lng'] - margin_value <= lng_value <= bounds['max_lng'] + margin_value
        ):
            matches.append(slug)
    return matches


def resolve_county_slug_for_point(lat, lng, preferred_slug=''):
    matches = counties_for_point(lat, lng)
    if not matches:
        return ''

    preferred = _normalize_slug(preferred_slug)
    if preferred and preferred in matches:
        return preferred
    if len(matches) == 1:
        return matches[0]

    lat_value = float(lat)
    lng_value = float(lng)
    best_slug = ''
    best_distance = None
    for slug in matches:
        centroid = COUNTY_CENTROIDS_BY_SLUG.get(slug)
        if centroid:
            center_lat = centroid['lat']
            center_lng = centroid['lng']
        else:
            bounds = COUNTY_BOUNDS_BY_SLUG[slug]
            center_lat = (bounds['min_lat'] + bounds['max_lat']) / 2.0
            center_lng = (bounds['min_lng'] + bounds['max_lng']) / 2.0
        distance = math.hypot(lat_value - center_lat, lng_value - center_lng)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_slug = slug
    return best_slug
