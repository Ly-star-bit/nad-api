"""Town names as they appear in shipping data - English, Chinese, the country's other language - grouped with the
name the national register uses (the first of each group). 'Vienna' is then searched as Wien, and 'Munich' is the
same town as München, not another one."""

from __future__ import annotations

import re

from .countries import _BY_NAME, fold

# A bare name that several towns share is in no group: 'Frankfurt' may be Frankfurt (Oder), 'Freiburg' Freiburg (Elbe),
# 'Newcastle' Newcastle-under-Lyme.
_GROUPS: dict[str, list[tuple[str, ...]]] = {
    "AT": [("Wien", "Vienna", "Vienne", "Viena", "维也纳", "維也納"), ("Salzburg", "Salzbourg", "萨尔茨堡"),
           ("Innsbruck", "因斯布鲁克"), ("Graz", "格拉茨"), ("Linz", "林茨"), ("Klagenfurt am Wörthersee", "Klagenfurt")],
    "BE": [("Bruxelles", "Brussel", "Brussels", "Bruselas", "Bruxelas", "Brüssel", "布鲁塞尔", "布魯塞爾"),
           ("Antwerpen", "Antwerp", "Anvers", "Amberes", "安特卫普"), ("Gent", "Ghent", "Gand", "根特"),
           ("Brugge", "Bruges", "布鲁日"), ("Liège", "Luik", "Lüttich", "Liege", "列日"), ("Leuven", "Louvain", "鲁汶"),
           ("Namur", "Namen"), ("Mons", "Bergen"), ("Mechelen", "Malines"), ("Kortrijk", "Courtrai"), ("Ieper", "Ypres"),
           ("Tournai", "Doornik"), ("Charleroi", "沙勒罗瓦")],
    "BG": [("София", "Sofia", "Sofiya", "索非亚"), ("Пловдив", "Plovdiv"), ("Варна", "Varna")],
    "CH": [("Zürich", "Zurich", "Zuerich", "Zurigo", "苏黎世", "蘇黎世"), ("Genève", "Geneva", "Genf", "Ginevra", "Geneve", "日内瓦",
            "日內瓦"), ("Bern", "Berne", "Berna", "伯尔尼"), ("Basel", "Bâle", "Basle", "Basilea", "巴塞尔"), ("Lausanne", "洛桑"),
           ("Luzern", "Lucerne", "Lucerna", "卢塞恩"), ("Biel/Bienne", "Biel", "Bienne"), ("Fribourg", "Freiburg im Üechtland"),
           ("Neuchâtel", "Neuenburg"), ("Sion", "Sitten"), ("Lugano", "卢加诺"), ("St. Gallen", "Sankt Gallen", "Saint-Gall")],
    "CY": [("Nicosia", "Lefkosia", "Λευκωσία"), ("Limassol", "Lemesos", "Λεμεσός"), ("Larnaca", "Larnaka", "Λάρνακα")],
    "CZ": [("Praha", "Prague", "Prag", "Praga", "布拉格"), ("Brno", "Brünn", "布尔诺"), ("Plzeň", "Pilsen", "Plzen"),
           ("Ostrava", "Ostrau"), ("Olomouc", "Olmütz"), ("Karlovy Vary", "Karlsbad", "Carlsbad"), ("České Budějovice", "Budweis"),
           ("Liberec", "Reichenberg")],
    "DE": [("München", "Munich", "Muenchen", "Monaco di Baviera", "慕尼黑"), ("Köln", "Cologne", "Koeln", "Colonia", "科隆"),
           ("Nürnberg", "Nuremberg", "Nuernberg", "Norimberga", "纽伦堡"), ("Berlin", "柏林"), ("Hamburg", "Hambourg", "Amburgo",
            "汉堡", "漢堡"), ("Frankfurt am Main", "Francfort", "Francoforte", "法兰克福"), ("Hannover", "Hanover",
            "Hanovre", "汉诺威"), ("Braunschweig", "Brunswick"), ("Düsseldorf", "Duesseldorf", "杜塞尔多夫"), ("Stuttgart", "斯图加特"),
           ("Leipzig", "莱比锡"), ("Dresden", "Dresde", "德累斯顿"), ("Bremen", "Brême", "不来梅"), ("Aachen", "Aix-la-Chapelle",
            "Aquisgrana", "亚琛"), ("Mainz", "Mayence", "美因茨"), ("Trier", "Trèves", "特里尔"), ("Koblenz", "Coblenz"),
           ("Regensburg", "Ratisbon", "雷根斯堡"), ("Dortmund", "多特蒙德"), ("Essen", "埃森"), ("Bonn", "波恩"),
           ("Heidelberg", "海德堡"), ("Mannheim", "曼海姆"), ("Karlsruhe", "卡尔斯鲁厄"), ("Augsburg", "奥格斯堡"),
           ("Freiburg im Breisgau", "弗莱堡"), ("Wiesbaden", "威斯巴登"), ("Duisburg", "杜伊斯堡"),
           ("Bochum", "波鸿"), ("Bielefeld", "比勒费尔德"), ("Münster", "Muenster", "明斯特"), ("Kiel", "基尔"),
           ("Lübeck", "Luebeck", "吕贝克"), ("Rostock", "罗斯托克"), ("Magdeburg", "马格德堡"), ("Erfurt", "爱尔福特"),
           ("Potsdam", "波茨坦"), ("Würzburg", "Wuerzburg", "维尔茨堡"), ("Ingolstadt", "因戈尔施塔特"),
           ("Wolfsburg", "沃尔夫斯堡"), ("Ulm", "乌尔姆"),
           # the big town a bare 'Mülheim' or 'Offenbach' means (still only compatible: the postcode decides)
           ("Mülheim an der Ruhr",), ("Offenbach am Main",), ("Ludwigshafen am Rhein",),
           ("Brandenburg an der Havel",)],
    "DK": [("København", "Copenhagen", "Kopenhagen", "Copenhague", "Copenaghen", "Kobenhavn", "Koebenhavn", "哥本哈根"),
           ("Aarhus", "Århus", "奥胡斯"), ("Odense", "欧登塞"), ("Aalborg", "Ålborg", "奥尔堡")],
    "EE": [("Tallinn", "Reval", "塔林"), ("Tartu", "Dorpat", "塔尔图")],
    "ES": [("Madrid", "马德里", "馬德里"), ("Barcelona", "Barcelone", "巴塞罗那", "巴塞隆納"), ("Sevilla", "Seville", "Séville",
            "塞维利亚"), ("Valencia", "València", "Valence", "瓦伦西亚", "巴伦西亚"), ("Zaragoza", "Saragossa", "Saragosse",
            "萨拉戈萨"), ("A Coruña", "La Coruña", "Corunna", "Coruña"), ("Girona", "Gerona", "赫罗纳"), ("Lleida", "Lérida"),
           ("Donostia/San Sebastián", "San Sebastián", "Donostia", "圣塞巴斯蒂安"), ("Vitoria-Gasteiz", "Vitoria", "Gasteiz"),
           ("Bilbao", "Bilbo", "毕尔巴鄂"), ("Málaga", "Malaga", "马拉加"), ("Palma", "Palma de Mallorca", "帕尔马"),
           ("Ourense", "Orense"), ("Alicante", "Alacant", "阿利坎特"), ("Castellón de la Plana", "Castelló de la Plana",
            "Castellón"), ("Granada", "格拉纳达"), ("Córdoba", "Cordoba", "科尔多瓦"), ("Murcia", "穆尔西亚"),
           ("Las Palmas de Gran Canaria",)],
    "FI": [("Helsinki", "Helsingfors", "赫尔辛基"), ("Espoo", "Esbo", "埃斯波"), ("Vantaa", "Vanda", "万塔"), ("Turku", "Åbo",
            "图尔库"), ("Tampere", "Tammerfors", "坦佩雷"), ("Oulu", "Uleåborg", "奥卢"), ("Vaasa", "Vasa"), ("Porvoo", "Borgå"),
           ("Jyväskylä", "Jyvaskyla"), ("Lahti", "Lahtis"), ("Kuopio",)],
    "FR": [("Paris", "Parigi", "París", "巴黎"), ("Marseille", "Marseilles", "Marsella", "马赛"), ("Lyon", "Lyons", "Lione", "里昂"),
           ("Strasbourg", "Straßburg", "Strassburg", "Estrasburgo", "斯特拉斯堡"), ("Dunkerque", "Dunkirk", "Dünkirchen"),
           ("Nice", "Nizza", "尼斯"), ("Bordeaux", "波尔多"), ("Toulouse", "Tolosa", "图卢兹"), ("Lille", "Rijsel", "里尔"),
           ("Nantes", "南特"), ("Montpellier", "蒙彼利埃"), ("Reims", "Rheims", "兰斯"), ("Rennes", "雷恩"),
           ("Le Havre", "勒阿弗尔"), ("Grenoble", "格勒诺布尔"), ("Dijon", "第戎"), ("Cannes", "戛纳")],
    "GB": [("London", "Londres", "Londra", "伦敦", "倫敦"), ("Edinburgh", "Édimbourg", "爱丁堡"), ("Manchester", "曼彻斯特"),
           ("Birmingham", "伯明翰"), ("Liverpool", "利物浦"), ("Glasgow", "格拉斯哥"), ("Cardiff", "Caerdydd", "加的夫"),
           ("Swansea", "Abertawe"), ("Leeds", "利兹"), ("Bristol", "布里斯托尔"), ("Oxford", "牛津"), ("Cambridge", "剑桥"),
           ("Belfast", "贝尔法斯特"), ("Newcastle upon Tyne", "纽卡斯尔"), ("Brighton and Hove",)],
    "GR": [("Athina", "Athens", "Athen", "Athènes", "Atene", "Αθήνα", "雅典"), ("Thessaloniki", "Salonica", "Θεσσαλονίκη",
            "塞萨洛尼基"), ("Peiraias", "Piraeus", "Πειραιάς", "比雷埃夫斯"), ("Irakleio", "Heraklion", "Ηράκλειο")],
    "HR": [("Zagreb", "Agram", "萨格勒布"), ("Split", "斯普利特"), ("Rijeka", "Fiume"), ("Dubrovnik", "Ragusa", "杜布罗夫尼克")],
    "HU": [("Budapest", "布达佩斯"), ("Debrecen", "德布勒森"), ("Szeged", "塞格德"), ("Pécs", "Pecs", "Fünfkirchen")],
    "IE": [("Dublin", "Baile Átha Cliath", "都柏林"), ("Cork", "Corcaigh", "科克"), ("Galway", "Gaillimh", "戈尔韦"),
           ("Limerick", "Luimneach")],
    "IS": [("Reykjavík", "Reykjavik", "雷克雅未克")],
    "IT": [("Roma", "Rome", "Rom", "罗马", "羅馬"), ("Milano", "Milan", "Mailand", "米兰", "米蘭"), ("Firenze", "Florence", "Florenz",
            "Florencia", "佛罗伦萨"), ("Venezia", "Venice", "Venedig", "Venise", "威尼斯"), ("Napoli", "Naples", "Neapel", "Nápoles",
            "那不勒斯"), ("Torino", "Turin", "Turín", "都灵"), ("Genova", "Genoa", "Genua", "Gênes", "热那亚"), ("Padova", "Padua"),
           ("Mantova", "Mantua"), ("Livorno", "Leghorn"), ("Siracusa", "Syracuse"), ("Bolzano", "Bozen", "博尔扎诺"),
           ("Trento", "Trent", "Trient"), ("Bologna", "博洛尼亚"), ("Verona", "维罗纳"), ("Pisa", "比萨"), ("Palermo", "巴勒莫"),
           ("Bari", "巴里"), ("Catania", "卡塔尼亚"), ("Brescia", "布雷西亚"), ("Bergamo", "贝加莫")],
    "LT": [("Vilnius", "Wilno", "维尔纽斯"), ("Kaunas", "Kowno", "考纳斯"), ("Klaipėda", "Klaipeda", "Memel")],
    "LU": [("Luxembourg", "Luxemburg", "Lëtzebuerg", "Luxembourg City", "Luxembourg-Ville", "Luxembourg Ville",
            "Ville de Luxembourg", "Luxemburg-Stadt", "卢森堡")],
    "LV": [("Rīga", "Riga", "里加"), ("Daugavpils", "Dünaburg")],
    "MC": [("Monaco", "Monte-Carlo", "Monte Carlo", "摩纳哥")],
    "MT": [("Valletta", "Il-Belt Valletta", "瓦莱塔")],
    "NL": [("Amsterdam", "阿姆斯特丹"), ("'s-Gravenhage", "Den Haag", "The Hague", "Hague", "La Haye", "La Haya", "海牙"),
           ("Rotterdam", "鹿特丹"), ("Utrecht", "乌得勒支"), ("Eindhoven", "埃因霍温"), ("'s-Hertogenbosch", "Den Bosch"),
           ("Groningen", "格罗宁根"), ("Maastricht", "马斯特里赫特"), ("Leiden", "Leyden", "莱顿")],
    "NO": [("Oslo", "奥斯陆"), ("Bergen", "卑尔根"), ("Trondheim", "Trondhjem", "特隆赫姆"), ("Stavanger", "斯塔万格")],
    "PL": [("Warszawa", "Warsaw", "Warschau", "Varsovie", "Varsavia", "Varsovia", "华沙", "華沙"), ("Kraków", "Krakow", "Cracow",
            "Krakau", "Cracovie", "Cracovia", "克拉科夫"), ("Łódź", "Lodz", "罗兹"), ("Wrocław", "Wroclaw", "Breslau",
            "弗罗茨瓦夫"), ("Gdańsk", "Gdansk", "Danzig", "格但斯克"), ("Poznań", "Poznan", "Posen", "波兹南"),
           ("Szczecin", "Stettin", "什切青"), ("Katowice", "Kattowitz", "卡托维兹"), ("Toruń", "Torun", "Thorn"),
           ("Białystok", "Bialystok"), ("Lublin", "卢布林"), ("Bydgoszcz", "Bromberg"), ("Gdynia", "格丁尼亚")],
    "PT": [("Lisboa", "Lisbon", "Lissabon", "Lisbonne", "Lisbona", "里斯本"), ("Porto", "Oporto", "波尔图"),
           ("Coimbra", "科英布拉"), ("Braga", "布拉加"), ("Funchal", "丰沙尔")],
    "RO": [("București", "Bucuresti", "Bucharest", "Bukarest", "Bucarest", "布加勒斯特"), ("Cluj-Napoca", "Cluj",
            "Klausenburg"), ("Iași", "Iasi"), ("Timișoara", "Timisoara", "Temeswar"), ("Brașov", "Brasov", "Kronstadt")],
    "RS": [("Beograd", "Belgrade", "Belgrad", "Београд", "贝尔格莱德"), ("Novi Sad", "Нови Сад", "诺维萨德"), ("Niš", "Nis", "Ниш")],
    "SE": [("Stockholm", "斯德哥尔摩"), ("Göteborg", "Gothenburg", "Goteborg", "Gotemburgo", "哥德堡"), ("Malmö", "Malmo",
            "马尔默"), ("Uppsala", "乌普萨拉")],
    "SI": [("Ljubljana", "Laibach", "Lubiana", "卢布尔雅那"), ("Maribor", "Marburg an der Drau")],
    "SK": [("Bratislava", "Pressburg", "Pozsony", "布拉迪斯拉发"), ("Košice", "Kosice", "Kassa", "Kaschau")],
    "UA": [("Київ", "Kyiv", "Kiev", "Киев", "基辅"), ("Львів", "Lviv", "Lvov", "Lwów", "Lemberg", "利沃夫"), ("Одеса", "Odesa",
            "Odessa", "Одесса", "敖德萨"), ("Харків", "Kharkiv", "Kharkov", "Харьков")],
    "AD": [("Andorra la Vella", "Andorra")],
    "VA": [("Città del Vaticano", "Vatican City", "Vatican", "梵蒂冈")],
}

_GROUP = {(cc, fold(name)): (cc, i) for cc, groups in _GROUPS.items() for i, g in enumerate(groups) for name in g}
_LOCAL = {(cc, fold(name)): g[0] for cc, groups in _GROUPS.items() for g in groups for name in g}
_STOP = {"am", "an", "der", "im", "in", "de", "la", "le", "sur", "upon", "on", "del", "di", "the", "nell", "nel", "sul"}
# Words that join a town's name to the rest of a longer one: 'Villeneuve-lès-Avignon', 'Bergen op Zoom',
# 'Saint-Martin-d'Hères', 'Newcastle-under-Lyme', 'Bergen auf Rügen'
_JOINERS = {"les", "lez", "sous", "en", "aux", "du", "des", "bei", "ob", "op", "aan", "bij", "nad", "pod", "and", "und",
            "d", "l", "auf", "super", "under", "cum"}
# A municipality's type after its name: Estonia's 'Voka alevik', Ireland's 'Kinsale Urban' (but Estonia's 'Tartu vald'
# is not Tartu)
_MUNICIPAL = {"vald", "linn", "alev", "alevik", "kula", "gemeente", "gmina", "kommune", "kommun", "urban", "rural"}
_CODE_RE = re.compile(r"[A-Z]{1,3}\.?")  # a canton or province code written after the town: 'Buchs ZH', 'Bergen L'
_ALIASES = {"co": "county"}  # Irish 'Co. Cork'
# What may follow a town's name in the same town: a district or arrondissement number ('Praha 1', 'Paris 11e', 'Lyon 3e
# arrondissement', 'Budapest XI', 'Wien 1. Bezirk'), a CEDEX. Any other word makes it another town, or one of several
# namesakes: 'Marquette-lez-Lille', 'Villeneuve-lès-Avignon', 'Buchs SG' (with no postcode, 'Buchs' may be any of them).
_DISTRICT_RE = re.compile(r"\d+(?:e|er|ere|eme|ieme|st|nd|rd|th)?|[ivx]{1,5}|arrondissement|arr|cedex|bezirk|kerulet")
_DISTRICT_BY_CC = {"DK": re.compile(r"[kvonsc]|sv|nv|no|so")}  # postal districts: 'København K', 'Aarhus C'
_BRACKET_RE = re.compile(r"\s*[(\[][^()\[\]]*[)\]]")


def local_city(name: str | None, cc: str) -> str:
    """'Vienna' (AT) -> 'Wien', '慕尼黑' (DE) -> 'München'; anything else unchanged, and so is a name that says which
    namesake it is ('Frankfurt (Oder)', 'Halle (Saale)')."""
    if _BRACKET_RE.search(name or ""):
        return name or ""
    return _LOCAL.get((cc, fold(name)), name or "")


def same_city(want: str | None, have: str | None, cc: str) -> bool | None:
    """Is the input's town the record's? True for the same town: exonyms ('Munich' is München), either part of a
    bilingual name ('Bruxelles - Brussel', a record's 'Elx/Elche'), the record's town with a district or qualifier the
    input adds ('Paris 11e' / 'Paris', 'Berlin-Mitte' / 'Berlin', 'Roma RM' / 'Roma', 'Lucan Co. Dublin' / 'Lucan').
    None when either is missing, or when the input is the start of the record's longer name ('Frankfurt' / 'Frankfurt
    am Main', 'Buchs' / 'Buchs ZH', 'Bergen' / 'Bergen (NH)'): which namesake it is, only the postcode or the located
    street can say. False for another town: one whose name contains the input's further on ('Wien' / 'Wiener
    Neustadt', 'Lille' / 'Marquette-lez-Lille', 'Ulm' / 'Neu-Ulm'), or a namesake with another qualifier ('Frankfurt
    (Oder)' / 'Frankfurt am Main', 'Buchs SG' / 'Buchs ZH', 'Praha 2' / 'Praha 1')."""
    if not want or not have:
        return None
    out: bool | None = False
    for x in _names(want):
        for y in _names(have, record=True):
            r = _same_town(x, y, cc)
            if r:
                return True
            if r is None:
                out = None
    # 'Berlin (Mitte)' is in Berlin; but a bracket narrows the name: 'Frankfurt (Oder)' is no bare 'Frankfurt'
    bare = _BRACKET_RE.sub("", want).strip()
    if bare and bare != want and not _BRACKET_RE.search(have) and any(
            _same_town(x, y, cc) is True for x in _names(bare) for y in _names(have, record=True)):
        return True
    return out


def district_differs(want: str | None, postcode: str | None, cc: str) -> bool:
    """'Dublin 2' is postal district D02: a record with an Eircode in D04 is in Dublin 4, another district."""
    m = re.fullmatch(r"dublin (\d{1,2}) ?(w?)", fold(want)) if cc == "IE" else None
    key = re.match(r"D(\d\d|6W)", re.sub(r"\s", "", postcode or "").upper())
    return bool(m and key) and f"{int(m.group(1)):02d}{m.group(2).upper()}".replace("06W", "6W") != key.group(1)


def shortened(want: str | None, have: str | None, cc: str) -> bool:
    """The input's town is the record's without the words that tell namesakes apart ('Buchs' / 'Buchs ZH',
    'Frankfurt' / 'Frankfurt am Main'): which one it is, only the postcode can say."""
    tw, th = _words(want or "", cc), _words(have or "", cc)
    return bool(tw) and len(tw) < len(th) and th[: len(tw)] == tw


def _names(name: str, record: bool = False) -> list[str]:
    """The name, each language's name of a bilingual town ('Bruxelles - Brussel', and a record's 'Elx/Elche'), and the
    town before its district (BEV's 'Graz,01.Bez.:Innere Stadt')."""
    parts = re.split(r"\s+-\s+|\s*/\s*" if record else r"\s+[-/]\s+", name)
    return list(dict.fromkeys(x for x in [name, *(parts if len(parts) > 1 else []), name.split(",")[0].strip()] if x))


def _same_town(x: str, y: str, cc: str) -> bool | None:
    w, h = fold(x), fold(y)
    if w == h:
        return True
    gw, gh = _GROUP.get((cc, w)), _GROUP.get((cc, h))
    if gw is not None and gw == gh:
        return True
    tw, th = (_words(n, cc) for n in (x, y))
    if not tw or not th:
        return False
    local = _DISTRICT_BY_CC.get(cc)

    def district(words: list[str]) -> bool:
        return all(_DISTRICT_RE.fullmatch(t) or (local and local.fullmatch(t)) for t in words)

    def hyphened(name: str, head: list[str]) -> bool:  # 'Berlin-Mitte', 'Bratislava-Staré Mesto'
        parts = re.split(r"\s*-\s*", name, maxsplit=1)
        return len(parts) == 2 and _words(parts[0], cc) == head
    if tw[: len(th)] == th:
        # the record's town with the input's district, canton or county after it ('Paris 11e', 'Roma RM', 'Lucan Co.
        # Dublin', 'Berlin-Mitte'); not a longer name of its own ('Newport Pagnell', 'Bury St Edmunds', 'Baden-Baden',
        # 'Bergen auf Rügen' are other towns)
        extra = tw[len(th):]
        if district(extra):
            return True
        if _joined(x, cc, len(th)) or (cc == "EE" and extra in (["vald"], ["linn"])):
            return False
        return (all(t in _MUNICIPAL for t in extra) or _coded(x, len(extra), upper=False) or extra[0] == "county"
                or _BY_NAME.get(" ".join(extra)) == cc or (gh is not None and hyphened(x, th)))
    if th[: len(tw)] == tw:
        # the input is the start of the record's name: a district of it; one of the namesakes a qualifier tells apart
        # ('Buchs ZH', 'Bergen (NH)', 'Voka alevik') or the big city's full name ('Frankfurt am Main'), None: the
        # postcode or the located street decides; else another town ('Saint-Martin-d'Hères', 'Bergen op Zoom', 'Stoke
        # Gifford', 'London Colney')
        extra = th[len(tw):]
        if district(extra):
            return True
        if cc == "EE" and extra in (["vald"], ["linn"]):  # Tartu linn and Tartu vald are two municipalities
            return False
        if _BRACKET_RE.search(y) or gh is not None:
            return None
        if _joined(y, cc, len(tw)):
            return False
        if _coded(y, len(extra)) or all(t in _MUNICIPAL for t in extra) or (gw is not None and hyphened(y, tw)):
            return None
    return False


def namesake(want: str | None, have: str | None, cc: str) -> bool:
    """The record's town is the input's with the qualifier that tells namesakes apart ('Buchs ZH', 'Bergen (NH)',
    'Frankfurt (Oder)'): without the postcode, the input may be any of them."""
    if not want or not have or same_city(want, have, cc) is not None:
        return False
    for x in _names(want):
        for y in _names(have, record=True):
            tw, th = _words(x, cc), _words(y, cc)
            if (tw and len(tw) < len(th) and th[: len(tw)] == tw
                    and (_BRACKET_RE.search(y) or _coded(y, len(th) - len(tw)))):
                return True
    return False


def _joined(name: str, cc: str, n: int) -> bool:
    """A joining word after the name's first n town words, before another word: 'Bergen op Zoom', 'Weil am Rhein'
    (n=1); not 'Bergen L'."""
    toks = [t for t in re.split(r"[\s/]+", fold(local_city(name, cc))) if t]
    while toks and toks[0].isdigit():
        toks.pop(0)
    i = k = 0
    while i < len(toks) and k < n:
        k += toks[i] not in _STOP
        i += 1
    return any(t in _STOP or t in _JOINERS for t in toks[i:-1])


def _coded(name: str, n: int, upper: bool = True) -> bool:
    """The name's last n words are codes, written apart: 'Buchs ZH', not 'Saint-Martin-Lys' (an input's may be in
    lower case: 'Roma rm')."""
    toks = name.split()
    if upper:
        return 0 < n < len(toks) and all(_CODE_RE.fullmatch(t) for t in toks[-n:]) and not " ".join(toks[:-n]).isupper()
    return 0 < n < len(toks) and all(_CODE_RE.fullmatch(t.upper()) for t in toks[-n:])


def _words(name: str, cc: str) -> list[str]:
    words = [_ALIASES.get(t, t) for t in re.split(r"[\s/]+", fold(local_city(name, cc))) if t and t not in _STOP]
    while words and words[0].isdigit():  # a postcode written into the town: '1200-815 Lisboa'
        words.pop(0)
    return words
