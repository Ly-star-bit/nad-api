"""Split a European address line into street, house number, unit, postcode and city.

Each country writes addresses in one of two orders - "Unter den Linden 77, 10117 Berlin" or
"18 bis rue de la République, 69002 Lyon" - with the postcode next to the city. Real input adds a recipient, a
company, a phone number, a floor or flat, a comma between the street and its number ("Calle de Alcalá, 45"), the
town first ("80331 München, Hauptstraße 12"), or a district instead of a postcode ("Na Příkopě 15, Praha 1"). The
providers do their own searching; this only has to find the parts that the answer is compared against - and never
lose the town, because without it a nationwide register search finds the same street in another town.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..address import prep
from .countries import Country, city_state, fold, is_country_word, strip_country
from .places import _LOCAL

# Unit / floor / staircase / building markers (German, French, Spanish, Portuguese, Italian, Dutch, Nordic, Baltic,
# Czech, Polish, English), matched on the text with its accents removed. Everything from the marker on is the unit.
_UNIT_WORDS = (
    r"apt|apartment|appt|app|appart|appartement|flat|unit|suite|ste|room|rm|floor|fl"
    r"|wohnung|whg|top|stiege|stg|tur|tuer|og|eg|dg|ug|stock|etage|etg|haus|hinterhaus|vorderhaus|seitenflugel"
    r"|gebaude|building|bldg|block|blk|bloc|bus|boite|bte|box|bat|batiment|esc|escalier|porte"
    r"|piso|puerta|planta|escalera|bajo|atico|portal|edificio|torre|dto|esq|izq|izda|dcha|r\s?/\s?c"
    r"|int|interno|scala|piano|pal|palazzina|local|loja|laden|tor"
    r"|lgh|lagenhet|leil|asunto|as|krt|korter|dz|dziv|byt|butas|lok|lokal|m|kv|hs|bg"
    r"|h\d{4}"
)
# Markers that need no value after them ("Kerkstraat 123 hs", "Calle Mayor 5 bajo", "Hauptstraße 1 Hinterhaus"), or
# carry their own ("H0201", a Norwegian flat)
_BARE_UNIT_WORDS = r"hs|bg|bajo|atico|rc|r\s?/\s?c|eg|dg|ug|hinterhaus|vorderhaus|seitenflugel|h\d{4}"
# Floors and flats that start with a number: "3. OG", "2º B", "3rd Floor", "3ème étage", "2 hoog", "3. th.", "st. tv.",
# "3 piętro", "3 krs", "2 izquierda"
_FLOOR = (r"\d+\s?[º°ª]|\d+o(?=[A-Za-z]?(?:\s|$))|\d+(?:st|nd|rd|th)\s+(?:floor|fl)\b|\d+\s*(?:e|er|ere|eme|ieme)\s+etage\b"
          r"|\d+(?:\.\s*|\s+)(?:og|eg|dg|stock|etage|etg|floor|fl|tr|trappor|hoog|th|tv|mf|sal|piso|planta|piano|pietro"
          r"|krs|kerros|izquierda|derecha|izq|izda|dcha|dcho)\b"
          r"|(?:st|kl)\.?\s*(?:tv|th|mf)\b")
_UNIT_RE = re.compile(rf"(?:^|(?<=[\s,/]))(?:(?:{_UNIT_WORDS})\b\.?|#|{_FLOOR})", re.I)
# what has to follow a unit word in "12 rue Ste-Catherine" style input, where the street comes after the number:
# "Apt 5", "Flat B", "porte gauche" are units; "Ste-Catherine", "Top Road", "M. Thorez", "Bus Station" are not
_UNIT_VALUE_RE = re.compile(r"\.?\s*[-:#]?\s*(?:n[o°º]\.?\s*)?(?:\d|[A-Za-z](?=[\s,.]|\d|$)"
                            r"|(?:gauche|droite|left|right|links|rechts|izquierda|derecha|izda|dcha|centro|rear|front)\b)",
                            re.I)
_UNIT_TOKEN_RE = re.compile(r"\.?\s*[-:#]?\s*(?:n[o°º]\.?\s*)?(?:\d[0-9A-Za-z/-]*|[A-Za-z](?=[\s,.]|$))\.?")
# A house number: digits, an optional letter ("12a", "11 A"), French "bis/ter/quater", and "/" or "-" parts
# ("4/6/8" in Poland, "12/3/4" in Austria, "8-H" in the Netherlands, "7-1" in Estonia).
_NUM = r"\d{1,5}(?:\s?[A-Za-z](?![A-Za-z]))?(?:\s?(?:bis|ter|quater)\b)?(?:\s?[-/]\s?[0-9A-Za-z]{1,4}(?![A-Za-z]{2}))*"
# Flats written after the house number that the unit markers can't see: Catalan floor and door ("401 1r 2a", "2n 1a"),
# Hungarian floor and door ("12. II. em. 3.", "12. fsz. 1."), a Dutch floor in Roman numerals ("2-III")
_FLAT_BY_COUNTRY = {
    "ES": re.compile(r"(?<=\d)\s+(\d{1,2}(?:r|n|t|e|è)\.?\s+\d{1,2}[aª]?\.?)$"),
    "AD": re.compile(r"(?<=\d)\s+(\d{1,2}(?:r|n|t|e|è)\.?\s+\d{1,2}[aª]?\.?)$"),
    "HU": re.compile(r"(?<=\d)\.?\s+((?:[IVX]{1,4}|\d{1,2})\.\s*em(?:elet)?\b.*"
                     r"|(?:fsz|fszt|földszint|mfsz|félemelet)\b.*)$", re.I),
    "NL": re.compile(r"(?<=\d)\s*-\s*([IVX]{1,4})$"),
}
_NO_MATCH = re.compile(r"(?!)")
_NUM_LAST_RE = re.compile(rf"^(?P<street>.*?\D)\s*,?\s+(?P<num>{_NUM})\s*$")
_NUM_FIRST_RE = re.compile(rf"^(?P<num>{_NUM})\s*,?\s+(?P<street>\D.*)$")
_NUM_INSIDE_RE = re.compile(rf"(?:^|\s)(?P<num>{_NUM})(?=\s|$)")
# "Nr. 12", "nº 5" / "n.º 5" (NFKC: "no 5" / "n.o 5"), "n. 10", "N° 10", "núm. 3" right before the number
_NUM_MARK_RE = re.compile(r"(?:^|(?<=[\s,]))(?:n\.?\s?[o°º]|n\.|nr|nro|num|numero|n[uú]m|n[uú]mero)\.?\s*(?=\d)", re.I)
# no number on purpose: Spanish / Portuguese "s/n", Italian "snc" / "s.n.c."
_NO_NUMBER_RE = re.compile(r"(?:^|(?<=[\s,]))(?:s\s?/\s?n|s\.?\s?n\.?\s?c\.?|sin n[uú]mero|senza numero|sem n[uú]mero)\.?(?=[\s,]|$)",
                           re.I)
_PHONE_LABEL = r"(?:tel|tél|telephone|téléphone|telefon|telefono|teléfono|phone|ph|mobile|mob|cell|handy|fax|电话|手机|電話|手機)"
_PHONE_RE = re.compile(rf"{_PHONE_LABEL}\.?\s*[:：]?\s*\+?\d[\d\s().\-/]{{4,}}\d|(?<![\w-])(?:\+|00|0)\d(?:[\s.\-/]?\d){{6,}}(?![\w-])",
                       re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BRACKET_RE = re.compile(r"[(\[【][^()\[\]【】]*[)\]】]")
_QUOTED_RE = re.compile(r"[\"“”«»„][^\"“”«»„]*[\"“”«»]")
# a company, organisation or c/o line, which is not the street even when it has a number ("3M Deutschland GmbH");
# folded, so "S.p.A." is "s p a". A part with a street word is a street ("Universitätsstraße", "Spa Road").
_LEGAL = (r"gmbh|ag|kg|ohg|ev|e v|ltd|limited|plc|llp|inc|llc|corp|sarl|sas|sasu|sa|s a|srl|s r l|spa|s p a|sl|s l|slu"
          r"|bv|b v|nv|n v|oy|oyj|ab|aps|asa|sp z o o|sro|s r o|kft|zrt|doo")
_ORG = r"company|hotel|restaurant|bank|universit\w*|hospital|klinik\w*|schule|school|museum"
_COMPANY_RE = re.compile(rf"\b(?:{_LEGAL}|{_ORG}|co|c o)\b")
# 'X AG' that is a company, not a town in Aargau: a business word, or a canton's name ("Treuhand AG", "Zug AG")
_AG_COMPANY = {
    "muster", "musterfirma", "treuhand", "holding", "immobilien", "consulting", "finanz", "invest", "handel", "handels",
    "bau", "transport", "logistik", "verwaltung", "trading", "service", "services", "technik", "software", "systems",
    "partner", "versicherung", "garage", "elektro", "auto", "beratung", "informatik", "medien", "druck", "metall",
    "zug", "zurich", "bern", "luzern", "uri", "schwyz", "obwalden", "nidwalden", "glarus", "freiburg", "solothurn",
    "basel", "schaffhausen", "appenzell", "graubunden", "aargau", "thurgau", "tessin", "waadt", "wallis", "neuenburg",
    "genf", "jura",
}
# a recipient written as its own part: not the town either
_RECIPIENT_RE = re.compile(r"^(?:(?:c\s?/\s?o|chez|z\.?\s?hd|zu h[aä]nden|attn?|t\.?\s?a\.?\s?v|herr|frau|mr|mrs|ms|mme"
                           r"|mlle|monsieur|madame|sig|sig\.?ra|sr|sra|dr|prof)\b|m\.\s)", re.I)
# a district rather than the town ("Damrak 1, Centrum, Amsterdam"; Estonian villages and boroughs before the parish)
_DISTRICT_WORDS = {"centrum", "centre", "center", "zentrum", "mitte", "centro", "innenstadt", "altstadt", "stadtmitte",
                   "city", "city centre", "city center", "town centre", "downtown", "old town", "centre ville",
                   "vieille ville", "centro storico", "centro historico", "casco antiguo", "binnenstad"}
# PO boxes and parcel lockers: not a building, so the registers can't have them
_PO_BOX = (r"p\.?\s*o\.?\s*box|post\s*office\s*box|postfach|postbus|postboks|postbox|apartado(?:\s+de\s+correos)?|apdo"
           r"|caixa\s+postal|skrytka\s+pocztowa|skr\.?\s*poczt|packstation|postfiliale|paketshop|casilla")
_PO_BOX_BY_COUNTRY = {"FR": r"|b\.?\s*p|boite\s+postale|cs", "BE": r"|b\.?\s*p|boite\s+postale", "LU": r"|b\.?\s*p|boite\s+postale",
                      "MC": r"|b\.?\s*p|boite\s+postale", "CH": r"|case\s+postale|casella\s+postale",
                      "IT": r"|casella\s+postale|c\.\s?p\.", "SE": r"|box", "FI": r"|pl|pb|box|postilokero"}
# English street words: in "10 Downing Street London SW1A 2AA" the town follows the last one - unless what follows is
# part of the name ("O'Connell Street Upper", "Notting Hill Gate", "Park Road West", "Hyde Park Corner")
_EN_STREET_WORDS = {"street", "st", "road", "rd", "lane", "ln", "avenue", "ave", "way", "close", "drive", "dr", "crescent",
                    "terrace", "gardens", "place", "square", "court", "grove", "park", "row", "hill", "walk", "mews",
                    "parade", "rise", "green", "view", "wharf", "quay", "yard", "circus", "embankment", "broadway", "end"}
_EN_STREET_TAIL = {"upper", "lower", "middle", "north", "south", "east", "west", "n", "s", "e", "w", "upr", "lwr", "lr",
                   "gate", "corner", "great", "little", "central", "extension", "ext"}
# A floor or part of the building written after an English street ("10 Park Road Ground Floor", "Top Flat", "Rear")
_EN_FLOOR_RE = re.compile(r"(?i)(?:(?:ground|first|second|third|top|basement|rear|front|back)(?:\s+(?:floor|flat|building))*"
                          r"|(?:floor|flat|building)\b)(?:\s+(?:flat|floor|building)\b)*(?=\s|$)")
# Towns whose districts are written as a number after the name ("Praha 1", "Dublin 2", "Paris 11", "Budapest 5")
_DISTRICT_TOWNS = {"praha", "prague", "prag", "brno", "plzen", "ostrava", "dublin", "wien", "vienna", "paris", "lyon",
                   "marseille", "budapest", "bratislava", "kosice"}
# A county or region written after the town ("Kinsale, Co. Cork", "Kendal, Cumbria", "München, Bayern"): it is not
# the town when a town is written too. Names that are also towns (Cork, Salzburg, Utrecht, Madrid) are left out.
_REGION_RE = re.compile(r"^(?:co|county|region|regione|provincia|province|prov|departement|dept|woj|wojewodztwo|comunidad"
                        r"|comunitat|kraj|judetul|jud)\b|(?:shire|\bkraj|\bmegye|\bmaakond|\bnovads|\bnov|\blan|\bfylke"
                        r"|\bapskritis|\bapskr)$")
_REGIONS = {
    # GB / IE counties
    "cumbria", "kent", "essex", "surrey", "sussex", "east sussex", "west sussex", "norfolk", "suffolk", "devon", "cornwall",
    "dorset", "somerset", "middlesex", "merseyside", "tyne and wear", "northumberland", "rutland", "isle of wight",
    "greater london", "greater manchester", "west midlands", "yorkshire", "north yorkshire", "south yorkshire",
    "west yorkshire", "east yorkshire", "east riding of yorkshire", "cleveland", "avon", "humberside", "fife", "lothian",
    "midlothian", "east lothian", "west lothian", "highland", "highlands", "strathclyde", "grampian", "tayside",
    "scottish borders", "dumfries and galloway", "argyll and bute", "gwynedd", "powys", "dyfed", "clwyd", "gwent",
    "glamorgan", "mid glamorgan", "south glamorgan", "west glamorgan", "vale of glamorgan", "anglesey", "ceredigion",
    "tyrone", "fermanagh", "kerry", "clare", "mayo", "meath", "offaly", "laois", "leitrim", "westmeath", "louth",
    "donegal", "tipperary",
    # DE / AT / CH
    "baden wurttemberg", "bayern", "bavaria", "brandenburg", "hessen", "hesse", "mecklenburg vorpommern", "niedersachsen",
    "lower saxony", "nordrhein westfalen", "north rhine westphalia", "nrw", "rheinland pfalz", "rhineland palatinate",
    "saarland", "sachsen", "saxony", "sachsen anhalt", "saxony anhalt", "schleswig holstein", "thuringen", "thuringia",
    "burgenland", "karnten", "carinthia", "niederosterreich", "lower austria", "oberosterreich", "upper austria",
    "steiermark", "styria", "tirol", "tyrol", "vorarlberg", "aargau", "thurgau", "graubunden", "grisons", "wallis",
    "valais", "waadt", "vaud", "tessin", "ticino", "basel landschaft", "baselland",
    # IT / ES / PT / FR
    "abruzzo", "basilicata", "calabria", "campania", "emilia romagna", "friuli venezia giulia", "lazio", "liguria",
    "lombardia", "lombardy", "marche", "molise", "piemonte", "piedmont", "puglia", "apulia", "sardegna", "sardinia",
    "sicilia", "sicily", "toscana", "tuscany", "trentino alto adige", "umbria", "valle d aosta", "veneto",
    "andalucia", "andalusia", "aragon", "asturias", "islas baleares", "illes balears", "canarias", "islas canarias",
    "cantabria", "castilla y leon", "castilla la mancha", "cataluna", "catalunya", "catalonia", "extremadura", "galicia",
    "navarra", "pais vasco", "euskadi", "la rioja", "ile de france", "auvergne rhone alpes", "bourgogne franche comte",
    "bretagne", "brittany", "centre val de loire", "corse", "grand est", "hauts de france", "normandie", "normandy",
    "nouvelle aquitaine", "occitanie", "pays de la loire", "provence alpes cote d azur", "paca",
    # NL / BE / PL
    "drenthe", "flevoland", "friesland", "fryslan", "gelderland", "limburg", "noord brabant", "noord holland",
    "overijssel", "zeeland", "zuid holland", "vlaanderen", "flandre", "flanders", "wallonie", "wallonia",
    "oost vlaanderen", "west vlaanderen", "vlaams brabant", "brabant wallon", "hainaut", "henegouwen",
    "mazowieckie", "malopolskie", "slaskie", "wielkopolskie", "dolnoslaskie", "pomorskie", "lodzkie",
    "zachodniopomorskie", "lubelskie", "podkarpackie", "kujawsko pomorskie", "podlaskie", "warminsko mazurskie",
    "swietokrzyskie", "opolskie", "lubuskie",
}
# Spanish and Italian provinces of one word, folded: in brackets after a town ('Getafe (Madrid)', 'Sesto San Giovanni
# (Milano)') they are a note, not part of the name; most are named after their capital, so not in _REGIONS
_PROVINCES = {
    "alava", "araba", "albacete", "alicante", "alacant", "almeria", "avila", "badajoz", "barcelona", "burgos", "caceres",
    "cadiz", "castellon", "castello", "cordoba", "coruna", "cuenca", "girona", "gerona", "granada", "guadalajara",
    "gipuzkoa", "guipuzcoa", "huelva", "huesca", "baleares", "jaen", "leon", "lleida", "lerida", "lugo", "madrid",
    "malaga", "murcia", "ourense", "orense", "palencia", "pontevedra", "salamanca", "segovia", "sevilla", "soria",
    "tarragona", "teruel", "toledo", "valencia", "valladolid", "bizkaia", "vizcaya", "zamora", "zaragoza",
    "agrigento", "alessandria", "ancona", "aosta", "arezzo", "asti", "avellino", "bari", "belluno", "benevento",
    "bergamo", "biella", "bologna", "bolzano", "brescia", "brindisi", "cagliari", "caltanissetta", "campobasso",
    "caserta", "catania", "catanzaro", "chieti", "como", "cosenza", "cremona", "crotone", "cuneo", "enna", "fermo",
    "ferrara", "firenze", "foggia", "frosinone", "genova", "gorizia", "grosseto", "imperia", "isernia", "latina", "lecce",
    "lecco", "livorno", "lodi", "lucca", "macerata", "mantova", "matera", "messina", "milano", "modena", "monza", "napoli",
    "novara", "nuoro", "oristano", "padova", "palermo", "parma", "pavia", "perugia", "pesaro", "pescara", "piacenza",
    "pisa", "pistoia", "pordenone", "potenza", "prato", "ragusa", "ravenna", "rieti", "rimini", "roma", "rovigo",
    "salerno", "sassari", "savona", "siena", "siracusa", "sondrio", "taranto", "teramo", "terni", "torino", "trapani",
    "trento", "treviso", "trieste", "udine", "varese", "venezia", "verbania", "vercelli", "verona", "vicenza", "viterbo",
}
# Postcodes that can start with 0, so a spreadsheet may have dropped the zeros ("1067 Dresden" for 01067)
_ZERO_PAD = {"DE", "FR", "ES", "IT", "FI", "NO", "LT", "PL", "SK", "RO", "UA"}
# The communes of the city-states that have several, folded: Luxembourg's communes (former names and the largest
# localities too), San Marino's castelli and largest curazie, Andorra's parishes. Written before the street with the
# country after it ("Bertrange, 100 route d'Arlon, Luxembourg"), they are the town, not the capital; one word that is
# none of them may be the recipient ("Marc, 23 rue ..., Luxembourg"), and the capital is kept
_CS_TOWNS = {
    "LU": {fold(x) for x in (
        "Beaufort", "Bech", "Beckerich", "Berdorf", "Bertrange", "Bettembourg", "Bettendorf", "Betzdorf", "Bissen",
        "Biwer", "Boulaide", "Bourscheid", "Bous", "Bous-Waldbredimus", "Clervaux", "Colmar-Berg", "Consdorf", "Contern",
        "Dalheim", "Diekirch", "Differdange", "Dippach", "Dudelange", "Echternach", "Ell", "Erpeldange-sur-Sûre",
        "Esch-sur-Alzette", "Esch-sur-Sûre", "Ettelbruck", "Feulen", "Fischbach", "Flaxweiler", "Frisange", "Garnich",
        "Goesdorf", "Grevenmacher", "Grosbous", "Groussbus-Wal", "Habscht", "Heffingen", "Helperknapp", "Hesperange",
        "Hobscheid", "Junglinster", "Käerjeng", "Bascharage", "Kayl", "Kehlen", "Kiischpelt", "Koerich", "Kopstal",
        "Lac de la Haute-Sûre", "Larochette", "Lenningen", "Leudelange", "Lintgen", "Lorentzweiler", "Mamer",
        "Manternach", "Mersch", "Mertert", "Mertzig", "Mondercange", "Mondorf-les-Bains", "Niederanven", "Nommern",
        "Parc Hosingen", "Hosingen", "Pétange", "Préizerdaul", "Putscheid", "Rambrouch", "Reckange-sur-Mess", "Redange",
        "Redange-sur-Attert", "Reisdorf", "Remich", "Roeser", "Rosport-Mompach", "Rosport", "Rumelange", "Saeul",
        "Sandweiler", "Sanem", "Schengen", "Schieren", "Schifflange", "Schuttrange", "Septfontaines", "Stadtbredimus",
        "Steinfort", "Steinsel", "Strassen", "Tandel", "Troisvierges", "Tuntange", "Useldange", "Vallée de l'Ernz",
        "Vianden", "Vichten", "Wahl", "Waldbillig", "Waldbredimus", "Walferdange", "Weiler-la-Tour", "Weiswampach",
        "Wiltz", "Wincrange", "Winseler", "Wormeldange", "Alzingen", "Belvaux", "Bereldange", "Bridel",
        "Capellen", "Ehlerange", "Fentange", "Foetz", "Heisdorf", "Helmsange", "Howald", "Itzig", "Lamadelaine",
        "Mondorf", "Munsbach", "Niederkorn", "Oberkorn", "Olm", "Rodange", "Schouweiler", "Senningerberg", "Soleuvre",
        "Sprinkange", "Holzem", "Hostert", "Moutfort", "Oetrange", "Remerschen", "Wasserbillig")},
    "SM": {fold(x) for x in (
        "Acquaviva", "Borgo Maggiore", "Chiesanuova", "Città di San Marino", "Domagnano", "Faetano", "Fiorentino",
        "Montegiardino", "Serravalle", "Dogana", "Falciano", "Rovereta", "Cailungo", "Murata", "Valdragone",
        "Gualdicciolo")},
    "AD": {fold(x) for x in (
        "Andorra la Vella", "Canillo", "Encamp", "Escaldes-Engordany", "Les Escaldes", "La Massana", "Ordino",
        "Sant Julià de Lòria", "Santa Coloma", "Pas de la Casa", "Soldeu", "Arinsal")},
}
# What makes a name of several words written before the street a town's ("Alcalá de Henares", "Ústí nad Labem",
# "Bergen op Zoom", "Bad Homburg", "La Rochelle", "Sesto San Giovanni") rather than a recipient's ("Jan Jansen",
# "Max Mustermann"), by country: a word joining it inside, a word starting it, the end of a German town's name. Not
# 'de' where people's names carry it ("Jan de Vries", "Maria de Souza", "Charles de Gaulle")
_DE_TOWN = (r"am|an|im|ob|bei", r"bad|sankt|st", r"stadt")  # not '-hausen', '-berg': "Peter Neuhausen", "Max Rosenberg"
_FR_TOWN = (r"sur|sous|les|lez|en", r"saint|sainte|st|ste|la|les", "")  # not 'Le': "Le Goff", "Le Roux"
_TOWN_SHAPE = {
    "DE": _DE_TOWN, "AT": _DE_TOWN, "LI": _DE_TOWN,
    "CH": (rf"{_DE_TOWN[0]}|{_FR_TOWN[0]}", rf"{_DE_TOWN[1]}|{_FR_TOWN[1]}", _DE_TOWN[2]),
    "FR": _FR_TOWN, "BE": (rf"op|aan|bij|{_FR_TOWN[0]}", _FR_TOWN[1], ""), "NL": (r"op|aan|bij", "", ""),
    "ES": (r"de|del|san|santa", r"san|santa|santo|sant|la|el|las|los", ""),
    "IT": (r"di|del|della|sul|san|santa", r"san|santa|santo|sant|la", ""),
    "CZ": (r"nad|pod", r"nove|nova|novy|stare|stara|stary", ""), "SK": (r"nad|pod", r"nove|nova|novy|stare|stara|stary", ""),
    "PL": (r"nad|pod", r"nowy|nowa|nowe|stary|stara|stare", ""), "GB": (r"upon|on", r"st", ""), "IE": ("", r"st", ""),
}
# lower-case words inside a town's name besides the joiners ("Mülheim an der Ruhr", "Castellón de la Plana"), and the
# particles of people's names ("Jan van der Berg", "Ursula von der Leyen", "João da Silva")
_TOWN_ARTICLES = {"der", "den", "dem", "la", "le", "les", "los", "las", "el", "l", "d"}
_NAME_PARTICLES = {"van", "von", "vom", "zu", "zum", "zur", "ten", "ter", "da", "do", "dos", "das"}
# first names that start a person's 'de' / 'di' name, not a town's: "María de la Cruz", "Marco di Stefano"
_GIVEN_NAMES = {
    "maria", "jose", "juan", "ana", "carmen", "antonio", "manuel", "francisco", "pedro", "luis", "carlos", "javier",
    "miguel", "isabel", "pilar", "teresa", "rosa", "laura", "lucia", "elena", "marta", "pablo", "jesus", "angel",
    "rafael", "fernando", "jorge", "alberto", "sergio", "david", "daniel", "alejandro", "marco", "mario", "giuseppe",
    "giovanni", "luca", "luigi", "francesco", "paolo", "andrea", "alessandro", "stefano", "roberto", "giorgio",
    "anna", "giulia", "francesca", "sara", "chiara", "paola", "jean", "pierre", "marie", "michel", "philippe",
}
# ... unless it starts like a business or an institution ("Société de Transport", "Comune di Milano", "Café de Paris")
_NOT_TOWN_START = {
    "societe", "familie", "famille", "family", "backerei", "baeckerei", "cafe", "bar", "residenza", "residence",
    "residencia", "maison", "cabinet", "atelier", "boulangerie", "pharmacie", "farmacia", "apotheke", "garage", "ets",
    "entreprise", "firma", "groupe", "gruppo", "grupo", "association", "associazione", "asociacion", "verein", "institut",
    "istituto", "instituto", "centre", "centro", "banque", "banca", "banco", "studio", "agence", "agenzia", "agencia",
    "praxis", "kanzlei", "ufficio", "bureau", "galerie", "galleria", "salon", "chateau", "casa", "bodega", "bodegas",
    "hostal", "ayuntamiento", "mairie", "comune", "gemeinde", "colegio", "escuela", "scuola", "ecole", "liceo", "lycee",
    "college", "museo", "musee", "clinica", "clinique", "ospedale", "camping", "club", "fondation", "fondazione",
    "fundacion", "stiftung", "ristorante", "trattoria", "pizzeria", "brasserie", "boutique", "tienda", "negozio", "haus",
}


@dataclass
class IntlAddress:
    raw: str
    country: str
    street: str = ""
    number: str = ""        # house number as written, spaces removed: "77", "12a", "18bis", "4/6/8", "8-H"
    unit: str = ""          # flat / floor / door text after the number
    postcode: str = ""
    city: str = ""
    extra: list[str] = field(default_factory=list)  # other parts (recipient, company, building, phone)
    warnings: list[str] = field(default_factory=list)
    po_box: str = ""        # "Postfach 1234": a mailbox, not a building
    no_number: bool = False  # written without a number on purpose: "s/n", "snc", a house name
    cedex: bool = False     # a French CEDEX / SP code: a mail sorting code, not the town's postcode

    @property
    def number_base(self) -> str:
        """'8-H' -> '8', '12/3/4' -> '12', '18bis' -> '18bis'."""
        return re.split(r"[-/]", self.number, maxsplit=1)[0]

    @property
    def number_ext(self) -> str:
        """The part after the first '-' or '/': '8-H' -> 'H', '12/3/4' -> '3/4'."""
        parts = re.split(r"[-/]", self.number, maxsplit=1)
        return parts[1] if len(parts) > 1 else ""


def _pc_regex(c: Country) -> re.Pattern:
    # optional "D-" / "F-" / "CH-" style prefix in front of numeric postcodes
    return re.compile(rf"(?<![\w-])(?:[A-Z]{{1,3}}-)?(?P<pc>{c.postcode})(?![\w-])", re.I)


def norm_number(text: str) -> str:
    """'11 A' -> '11A', '18 bis' -> '18bis', '4 / 6' -> '4/6'."""
    return re.sub(r"\s+", "", text or "")


def _plain(text: str) -> str:
    """Accents removed, same length: the unit regex then matches 'Bât', 'Tür', 'lägenhet', 'étage'."""
    return "".join(unicodedata.normalize("NFKD", ch)[0] for ch in text)


def _unit_at(seg: str, c: Country | None = None) -> re.Match | None:
    """The unit marker that starts the unit: after some text that has a digit ("Top 5" in "Hauptstraße 12 Top 5",
    not the "M" of a street called "M 4"); in "12 rue Ste-Catherine" order only one followed by a value."""
    plain = _plain(seg)
    lead = _NUM_FIRST_RE.match(seg)
    pos = 0
    while m := _UNIT_RE.search(plain, pos):
        pos = m.start() + 1  # "Ringstraße 7 EG links": the floor "7 EG" starts at the house number, the unit at "EG"
        if m.start() == 0 or not re.search(r"\d", seg[: m.start()]):
            continue
        word = not re.match(r"[\d#]|(?:st|kl)\.?\s*(?:tv|th|mf)\b", m.group(), re.I)
        if (lead and word and not re.search(r"\d", seg[lead.end("num"): m.start()])
                and not _UNIT_VALUE_RE.match(plain, m.end()) and not re.fullmatch(_BARE_UNIT_WORDS, m.group().strip(" .,"), re.I)):
            continue
        return m
    return None


def is_unit_line(seg: str) -> bool:
    """A part that is only a flat / floor / building: "Flat 3", "3. OG", "2º B", "3rd Floor", "Bât. 2", "Haus 3"."""
    plain = _plain(seg.strip())
    m = _UNIT_RE.match(plain)
    if not m:
        return False
    if re.match(r"[\d#]|(?:st|kl)\.?\s*(?:tv|th|mf)\b", m.group(), re.I):
        return not _NUM_FIRST_RE.match(seg.strip()) or bool(re.fullmatch(rf"(?:{_FLOOR})\.?(?:\s*\w{{1,3}}\.?)?", plain, re.I))
    return bool(_UNIT_VALUE_RE.match(plain, m.end())) or bool(re.fullmatch(_BARE_UNIT_WORDS, m.group().strip(" .,"), re.I))


def is_unit_only(text: str) -> bool:
    return is_unit_line(text or "")


def po_box(seg: str, c: Country) -> bool:
    """'Postfach 1234', 'BP 123', 'PL 123' (Finland); a Swiss 'Postfach' on its own has no number."""
    words = rf"(?:{_PO_BOX}{_PO_BOX_BY_COUNTRY.get(c.code, '')})"
    return bool(re.match(rf"{words}\s*\.?\s*(?:n[o°º]\.?\s*)?\d", seg.strip(), re.I)
                or len(seg.strip()) >= 6 and re.fullmatch(rf"{words}\.?", seg.strip(), re.I))


def _street_word(text: str) -> bool:
    from .match import street_parts  # match imports this module
    return bool(street_parts(text)[1])


def split_street(seg: str, c: Country) -> tuple[str, str, str]:
    """Street part -> (street, number, unit)."""
    street, number, unit, _ = _split(seg, c)
    return street, number, unit


def _split(seg: str, c: Country, en_town: bool = False) -> tuple[str, str, str, str]:
    """Street part -> (street, number, unit, rest): rest is what followed the street that is neither the number nor
    a unit - the town, when no comma separates it ("Hauptstraße 12 München", "10 Downing Street London SW1A 2AA";
    the English one only with `en_town`)."""
    seg = _NUM_MARK_RE.sub("", seg).strip(" ,")
    unit = ""
    if m := _unit_at(seg, c):
        unit, seg = seg[m.start():].strip(" ,-/"), seg[: m.start()].strip(" ,-/")
    if not unit and (m := _FLAT_BY_COUNTRY.get(c.code, _NO_MATCH).search(seg)):
        unit, seg = m.group(1).strip(), seg[: m.start()].strip(" ,-/")
    seg = re.sub(r"(?<=\d)\.(?=\s|$)", "", seg)  # Hungarian "Andrássy út 12."
    if c.code == "LV":  # "386 k2" / "386 k.2" / "386 korpuss 2": block 2 of house 386, "386 k-2" in the register
        seg = re.sub(r"(?<=\d)\s*(?:korpuss|korp|k)\s*[.-]?\s*(?=\d)", " k-", seg, flags=re.I)
    if c.code == "FI" and (m := re.fullmatch(r"(?P<street>.*?\D)\s+(?P<num>\d{1,4})\s+(?P<st>[A-Z])", seg)):
        # "Mannerheimintie 30 A": staircase A of number 30 (a house letter is written "30a")
        return m.group("street").strip(" ,"), m.group("num"), " ".join(p for p in (m.group("st"), unit) if p), ""
    if c.code in ("FI", "SE", "EE") and (m := re.fullmatch(r"(?P<street>.*?\D)\s+(?P<num>\d{1,4})\s+(?P<st>[A-Za-z])\s*(?P<flat>\d{1,4})", seg)):
        # Finnish "Mannerheimintie 30 A 5": staircase A, flat 5
        return m.group("street").strip(" ,"), m.group("num"), " ".join(p for p in (f"{m.group('st')} {m.group('flat')}", unit) if p), ""
    orders = (_NUM_FIRST_RE, _NUM_LAST_RE) if c.number_first else (_NUM_LAST_RE, _NUM_FIRST_RE)
    for rx in orders:
        if m := rx.match(seg):
            street, rest = m.group("street").strip(" ,"), ""
            if rx is _NUM_FIRST_RE and c.number_first and en_town:
                street, rest = _en_town(street, c)
            return street, norm_number(m.group("num")), unit, rest
    if m := _NUM_INSIDE_RE.search(seg):  # the number in the middle: what follows it is the town (or a leftover)
        before, after = seg[: m.start()].strip(" ,"), seg[m.end():].strip(" ,")
        if not re.search(r"[^\W\d_]", before):  # "12 Main Street Dublin 2" style, or a lone number first
            street, rest = _en_town(after, c) if c.number_first and en_town else (after, "")
            return street, norm_number(m.group("num")), unit, rest
        if c.number_first:
            return f"{before} {after}".strip(), norm_number(m.group("num")), unit, ""
        return before, norm_number(m.group("num")), unit, after
    return seg, "", unit, ""


def _en_town(street: str, c: Country) -> tuple[str, str]:
    """'Downing Street London' -> ('Downing Street', 'London'): in English-speaking countries the street ends with
    its type word. French 'rue de Rivoli Paris' -> ('rue de Rivoli', 'Paris') only for a known town after a name
    that is not 'de' ('rue de Paris')."""
    words = street.split()
    if c.code == "FR":
        for n in (3, 2, 1):
            if len(words) - n >= 2 and (c.code, fold(" ".join(words[-n:]))) in _LOCAL \
                    and not re.fullmatch(r"(?i)de|du|des|d'.*|la|le|les|l'.*|a|à|au|aux|sur|en|et", words[-n - 1]):
                return " ".join(words[:-n]), " ".join(words[-n:])
        return street, ""
    if c.code not in ("GB", "IE", "JE", "GG", "IM", "GI", "MT"):
        return street, ""
    ends = [i for i, w in enumerate(words) if fold(w) in _EN_STREET_WORDS and i > 0]
    if not ends:
        return street, ""
    if len(ends) > 1 and fold(words[ends[-1]]) == "st" and ends[-2] == ends[-1] - 1 and ends[-1] + 1 < len(words):
        ends.pop()  # "High Street St Albans": Saint, the town's first word
    end = ends[-1] + 1
    while end < len(words) and fold(words[end]) in _EN_STREET_TAIL:  # "O'Connell Street Upper", "Notting Hill Gate"
        end += 1
    if end < len(words):
        return " ".join(words[:end]), " ".join(words[end:])
    return street, ""


def _number_only(seg: str, c: Country) -> bool:
    """A part that is just the house number (and maybe a unit): '45', 'nº 5', '12 bis', '5 - 3º B', '10 -'."""
    if re.fullmatch(_NUM, _NUM_MARK_RE.sub("", seg).strip(" ,-")):  # "18 bis": number-first order reads 'bis' as the street
        return True
    street, number, _unit, rest = _split(seg, c)
    return bool(number) and not re.search(r"[^\W\d_]", street.replace("-", "")) and not rest


def _split_unit_first(seg: str) -> list[str]:
    """'Flat 3 10 Downing Street' -> ['Flat 3', '10 Downing Street']."""
    plain = _plain(seg)
    m = _UNIT_RE.match(plain)
    if not m or re.match(r"[\d#]", m.group()):
        return [seg]
    v = _UNIT_TOKEN_RE.match(plain, m.end())
    if not v:
        return [seg]
    rest = seg[v.end():].strip(" ,-")
    if re.search(r"\d", rest) and re.search(r"[^\W\d_]{2}", rest) and not is_unit_line(rest):
        return [seg[: v.end()].strip(), rest]
    return [seg]


def _clean(seg: str, cc: str = "") -> tuple[str, list[str]]:
    """Take brackets, quotes, phone numbers and e-mail addresses out of a part -> (part, what was taken out)."""
    junk: list[str] = []

    def cut(m: re.Match) -> str:
        junk.append(m.group().strip(" ()[]【】\"“”«»„"))
        return " "

    s = seg.strip()
    if re.fullmatch(r"[\"“”«»„].*[\"“”«»]", s):  # the whole part quoted
        s = s[1:-1]
    qual = ""
    if m := _qualifier(s, cc):
        s, qual = s[: m.start()], f" ({m.group(1)})"
    for rx in (_BRACKET_RE, _QUOTED_RE, _EMAIL_RE):
        s = rx.sub(cut, s)
    s = _PHONE_RE.sub(lambda m: cut(m) if len(re.sub(r"\D", "", m.group())) >= (6 if re.match(_PHONE_LABEL, m.group(), re.I) else 8)
                      else m.group(), s)
    return re.sub(r"\s+", " ", s).strip(" ,-:;") + qual, [j for j in junk if j]


def _qualifier(s: str, cc: str = "") -> re.Match | None:
    """'Frankfurt (Oder)', '15230 Frankfurt (Oder)', 'Halle (Saale)', Dutch 'Bergen (NH)': a bracket that is part of
    the town's name - without it the name is another town's. Not 'Roma (RM)', 'Hauptstraße 12 (Hinterhaus)', nor the
    town's own name, region or province ('Málaga (Málaga)', 'Graz (Steiermark)', 'Getafe (Madrid)'): with those the
    registers find nothing."""
    m = re.search(rf"(?<=[^\W\d_])\s*\(([^\W\d_]{{{1 if cc == 'NL' else 3},}}\.?)\)$", s)
    if not m or not m.group(1)[0].isupper() or is_country_word(m.group(1)):
        return None
    town = re.sub(r"^.*\d\S*\s*", "", s[: m.start()])
    q = fold(m.group(1))
    if not _place_shape(town) or q == fold(town) or _region(q) or q in _PROVINCES:
        return None
    return m


def _normalize(text: str) -> str:
    t = prep(text).replace("。", ",").replace(";", ",")
    t = re.sub(r"(?<=\d)\s*:\s*(?=\d{3})", ", ", t)        # "Hauptstraße 12：80331 München"
    t = re.sub(r"(?<=\d)\s*号(?=[\s,]|$)", "", t)          # Chinese "12号"
    return re.sub(r"(?<=[^\W\d_]\.)(?=\d)", " ", t)        # "Hauptstr.12", "Nr.12"


def _pad(digits: str, words: str, c: Country) -> str | None:
    """'1067' + 'Dresden' -> '01067' when the country's postcodes can start with zeros a spreadsheet dropped;
    Polish '00001' -> '00-001'. At most two zeros, and only before a town: '2 Izquierda' is a floor and '123 Local' a
    shop, not postcode 00002 / 00123."""
    if c.code not in _ZERO_PAD or not words or not _place_shape(words) \
            or re.fullmatch(rf"(?:{_UNIT_WORDS})\.?", _plain(words.strip()), re.I):
        return None
    for n in range(len(digits), min(len(digits) + 3, 7)):
        cand = digits.zfill(n)
        if c.code == "PL" and n == 5:
            cand = f"{cand[:2]}-{cand[2:]}"
        if cand != digits and re.fullmatch(c.postcode, cand):
            return cand
    return None


def _place_shape(text: str) -> bool:
    """Words only, no street type: the town next to a postcode ("München", "Paris Cedex 08", "Milano MI")."""
    return bool(re.search(r"[^\W\d_]", text)) and not re.search(r"\d", re.sub(r"(?i)\bcedex\s*\d{1,2}\b", "", text)) \
        and not _street_word(text)


def _unit_after_town(text: str) -> tuple[str, str] | None:
    """'Berlin Wohnung 5' -> ('Berlin', 'Wohnung 5'), 'Amsterdam 3 hoog' -> ('Amsterdam', '3 hoog'): a flat or floor
    written after the town."""
    plain = _plain(text)
    for m in _UNIT_RE.finditer(plain):
        town, unit = text[: m.start()].strip(" ,-"), text[m.start():].strip()
        if town and not re.search(r"\d", town) and _place_shape(town) and is_unit_line(unit):
            return town, unit
    return None


def _find_postcode(a: IntlAddress, segs: list[str], c: Country) -> list[str]:
    """Postcode and town, searched from the end ("..., 10117 Berlin", "London SW1A 2AA", "Rīga, LV-1050", or first:
    "80331 München, Hauptstraße 12") -> the remaining parts. A flat after the town ("10117 Berlin Wohnung 5") is
    returned as a part of its own."""
    rx = _pc_regex(c)
    for i in range(len(segs) - 1, -1, -1):
        seg = segs[i]
        found = list(rx.finditer(seg))
        if not found:
            continue
        m = found[-1]
        before, after = seg[: m.start()].strip(" ,"), seg[m.end():].strip(" ,")
        if cut := _unit_after_town(after):
            after, unit = cut
            return _find_postcode(a, segs[:i] + [f"{before} {m.group()} {after}".strip()] + segs[i + 1:], c) + [unit]
        if len(segs) == 1:
            if before and not re.search(r"\d", before) and _place_shape(before) and not _not_town(before, c) \
                    and (r := _read_town(before, c)) and _place_name(before, r, c):  # not "Max Mustermann 10117 ..."
                street, number, _unit, rest = _split(after, c)
                if number and re.search(r"[^\W\d_]", street) and not rest:  # "Berlin 10117 Unter den Linden 77"
                    a.postcode, a.city = m.group("pc").upper(), before
                    return [after]
            if not before and (_place_shape(after) or _district(after)):  # only the town is left ("Postfach 12 34, 10115 Berlin")
                a.postcode, a.city = m.group("pc").upper(), after
                return []
            if a.po_box and not after and _place_shape(before):  # "PO Box 123, London SW1A 2AA"
                a.postcode, a.city = m.group("pc").upper(), before
                return []
            if not before and (n := _town_words(after, c)):  # "1061 Budapest Andrássy út 12": big to small, no commas
                words = after.split()
                a.postcode, a.city = m.group("pc").upper(), " ".join(words[:n])
                return [" ".join(words[n:])]
            if not re.search(r"\d", before):
                return segs  # a number that is not a postcode
            a.postcode, a.city = m.group("pc").upper(), after
            return [before]
        town = after if after and (_place_shape(after) or not before) else before if _place_shape(before) else ""
        if i == 0 and not (town and not re.search(r"\d", f"{before} {after}".replace(town, ""))):
            continue  # the street's own number ("Hauptstraße 12345", "12 Main St")
        if i == 0 and before and not any(_numbered_street(s, c) for s in segs[1:]):
            continue  # "Laugavegur 120, Reykjavík": a number ending the first part is the house number
        a.postcode, a.city = m.group("pc").upper(), town
        rest = (before if town == after else after).strip(" ,-")
        if rest and (re.search(r"\d", rest) or _street_word(rest)):
            segs[i] = rest  # "Via Garibaldi, 10 - 20121 Milano": keep the 10; "12, rue de la Paix 75002 Paris": the street
        else:
            del segs[i]
            if rest:
                a.extra.append(rest)
        if a.city and a.city == before and _region(before) and i - 1 >= 1 and _place_shape(segs[i - 1]) \
                and not is_unit_line(segs[i - 1]) and not _region(segs[i - 1]) and not _not_town(segs[i - 1], c):
            a.extra.append(a.city)  # "Cambridge, Cambridgeshire CB2 3DT": the town is written before the county
            a.city = segs.pop(i - 1)
        if not a.city:  # a postcode of its own: the town is the next part, else the one before (never a numbered one)
            if i < len(segs) and _place_shape(segs[i]) and not is_unit_line(segs[i]):
                a.city = segs.pop(i)
            elif i - 1 >= 1 and _place_shape(segs[i - 1]) and not is_unit_line(segs[i - 1]):
                j = i - 1  # "Kinsale, Co. Cork, P17 X123": not the county when the town is written before it
                while j - 1 >= 1 and _region(segs[j]) and _place_shape(segs[j - 1]) and not is_unit_line(segs[j - 1]):
                    j -= 1
                a.city = segs.pop(j)
        return segs
    return segs


def _town_words(text: str, c: Country) -> int:
    """How many first words of 'Budapest Andrássy út 12' are a known town (and its district number) followed by a
    numbered street; 0 if none."""
    words = text.split()
    for n in (3, 2, 1):
        if len(words) > n and (c.code, fold(" ".join(words[:n]))) in _LOCAL and _numbered_street(" ".join(words[n:]), c):
            if len(words) > n + 1 and _district(" ".join(words[: n + 1])) and words[n + 1][0].isupper() \
                    and _numbered_street(" ".join(words[n + 1:]), c):
                return n + 1  # "110 00 Praha 1 Na Příkopě 15": the district with the town; not "Praha 17. listopadu 2"
            return n
    return 0


def _numbered_street(seg: str, c: Country) -> bool:
    """A part with a street and its number (not a flat, a PO box or a district like 'Praha 1')."""
    if not re.search(r"\d", seg) or is_unit_line(seg) or po_box(seg, c) or _district(seg):
        return False
    street, number, _unit, _rest = _split(seg, c)
    return bool(number) and bool(re.search(r"[^\W\d_]", street))


def _district(text: str) -> bool:
    """'Praha 1', 'Dublin 2', 'Paris 11e': a district of a town that numbers them."""
    m = re.fullmatch(r"(\D{2,}?)\s*(\d{1,2})(?:e|er|eme|ème|\.)?", text.strip())
    return bool(m) and fold(m.group(1)) in _DISTRICT_TOWNS


def _region(text: str) -> bool:
    """'Co. Cork', 'County Durham', 'Cumbria', 'Lancashire', 'Bayern', 'Lombardia', 'Pest megye': not a town."""
    f = fold(text)
    return f in _REGIONS or bool(_REGION_RE.search(f))


def _merge_numbers(segs: list[str], c: Country, has_city: bool) -> list[str]:
    """'Calle de Alcalá, 45' / '42, Place Guillaume II': join a part that is only the number with its street."""
    segs = list(segs)
    i = 0
    while i < len(segs):
        if len(segs) > 1 and re.search(r"\d", segs[i]) and _number_only(segs[i], c):
            for j in ((i + 1, i - 1) if c.number_first else (i - 1, i + 1)):
                if not 0 <= j < len(segs):
                    continue
                s = segs[j]
                # a street with a date in its name ("Via 4 Novembre", "rue du 11 Novembre 1918") has digits too, but
                # not where the country writes the house number
                if (re.search(r"\d", s) and ((_NUM_FIRST_RE if c.number_first else _NUM_LAST_RE).match(s) or _district(s))
                        or not re.search(r"[^\W\d_]", s) or is_unit_line(s) or _company(s)):
                    continue
                # "Rue Inexistante, 2, Paris": with no postcode, the last part is the town, not the street
                if j == len(segs) - 1 and not has_city and not _street_word(s):
                    continue
                segs[min(i, j)] = f"{segs[j]} {segs[i]}" if j < i else f"{segs[i]} {segs[j]}"
                del segs[max(i, j)]
                i = min(i, j)
                break
        i += 1
    return segs


def _pick_street(segs: list[str], c: Country) -> int | None:
    """The part that is the street: it has a house number, best with a street word ('Str.', 'rue') and the number
    where the country writes it; a company, a building or a flat line is not it."""
    best, best_score = None, None
    for i, s in enumerate(segs):
        if not re.search(r"\d", s) or is_unit_line(s) or po_box(s, c) or _district(s):
            continue
        street, number, _unit, _rest = _split(s, c)
        if not number or not re.search(r"[^\W\d_]", street):
            continue
        order = (_NUM_FIRST_RE if c.number_first else _NUM_LAST_RE).match(_NUM_MARK_RE.sub("", s).strip())
        score = 2 * _street_word(street) + bool(order) - 3 * _company(s)
        if best_score is None or score > best_score:
            best, best_score = i, score
    return best


def _company(seg: str) -> bool:
    """A company's legal form or c/o in a part without a street word ('Universitätsstraße', 'Spa Road' are streets)."""
    return bool(_COMPANY_RE.search(fold(seg))) and not _street_word(seg)


def parse_line(text: str, c: Country) -> IntlAddress:
    t = _normalize(text)
    a = IntlAddress(raw=t, country=c.code)
    raw = [s.strip() for s in t.split(",") if s.strip()]
    segs, removed = strip_country(raw, c.code)
    if segs and removed and any(re.sub(r"\s", "", segs[-1] + removed[0]) == re.sub(r"\s", "", r) for r in raw) \
            and _qualifier(f"{segs[-1]} {removed[0]}", c.code):
        segs[-1] = f"{segs[-1]} {removed.pop(0)}"  # strip_country takes "(Oder)" off "Frankfurt (Oder)" as a note
    a.extra += [r for r in removed if not is_country_word(r)]
    cleaned = []
    for s in segs:
        s, junk = _clean(s, c.code)
        a.extra += junk
        if s:
            cleaned.append(s)
    segs = [p for s in cleaned for p in _split_unit_first(s)]
    if not segs:
        return _finish(a, c, removed)
    for s in [s for s in segs if po_box(s, c)]:  # "Postfach 1234": a mailbox; any street with it is still checked
        a.po_box = a.po_box or s
        segs.remove(s)
    segs = _find_postcode(a, segs, c)
    segs = _merge_numbers(segs, c, bool(a.city))
    street_i = _pick_street(segs, c)
    rest = ""
    if street_i is not None:
        # "10 Downing Street London SW1A 2AA", "12 Main Street Dublin 2", "10 Downing Street London": the town glued
        # to an English street when no other part can be the town; without a postcode only a place-shaped remainder
        en_town = not a.city and all(is_unit_line(s) for s in segs[street_i + 1:])
        a.street, a.number, a.unit, rest = _split(segs[street_i], c, en_town)
        if rest and c.code in ("GB", "IE") and (m := _EN_FLOOR_RE.match(rest)):  # the flat, not the town
            n = m.end() if re.match(r"\s*(?:$|[A-Z])", rest[m.end():]) else len(rest)  # "Rear of Shop": all of it
            a.unit, rest = ", ".join(p for p in (a.unit, rest[:n].strip()) if p), rest[n:].strip()
        if rest and not a.postcode and not _district(rest) and c.number_first \
                and not (_place_shape(rest) and not _not_town(rest, c)):
            a.street, a.number, a.unit, rest = _split(segs[street_i], c)
    else:  # no number at all: the first part with a street word, else the first one that isn't a unit
        street_i = next((i for i, s in enumerate(segs) if _street_word(s) and not is_unit_line(s)), None)
        if street_i is None:
            street_i = next((i for i, s in enumerate(segs) if not is_unit_line(s)), None)
        if street_i is not None:
            a.street = segs[street_i]
            if _NO_NUMBER_RE.search(a.street):
                a.street, a.no_number = _NO_NUMBER_RE.sub("", a.street).strip(" ,-"), True
    town_i = _pick_town(a, segs, street_i, rest, c, bool(city_state(removed)))
    if re.search(r"[^\W_]", rest) and town_i != -1:
        a.extra.append(rest)  # text after the number that is neither the unit nor the town is kept, not dropped
    for i, s in enumerate(segs):
        if i == street_i or i == town_i:
            continue
        if is_unit_line(s):
            a.unit = ", ".join(p for p in (a.unit, s) if p)
        else:
            a.extra.append(s)
    return _finish(a, c, removed)


def _pick_town(a: IntlAddress, segs: list[str], street_i: int | None, rest: str, c: Country,
               cs: bool = False) -> int | None:
    """The town when no postcode part gave it -> its index in segs (-1: the rest glued to the street), a.city set.
    After the street it is the first part that reads as a town, passing over a district ("Damrak 1, Centrum,
    Amsterdam") and a county or region ("Kinsale, Co. Cork") when a town follows or precedes; between two plain names
    the first stays (in "Bahnhofstrasse 1, Winterthur, Zürich" the second is the canton). With nothing after the
    street, the part right before it ("Debrecen, Váci utca 10.", big-to-small order) when it reads as a place - not
    a recipient ("Jan Jansen, Damrak 1"); `cs`: a city-state's name was written, which a recipient never replaces."""
    if a.city or street_i is None:
        return None
    cands = ([(-1, rest)] if rest else []) + [(i, s) for i, s in enumerate(segs) if i > street_i and not is_unit_line(s)]
    reads = [(i, s, r) for k, (i, s) in enumerate(cands) if not _not_town(s, c)
             and (r := _read_town(s, c, later=any(_place_shape(x) for _, x in cands[k + 1:])))]
    if not cands and street_i > 0 and (not a.postcode or _district(segs[street_i - 1])) \
            and not is_unit_line(segs[street_i - 1]) and not _not_town(segs[street_i - 1], c) \
            and (r := _read_town(segs[street_i - 1], c)) and _place_name(segs[street_i - 1], r, c, cs):
        reads = [(street_i - 1, segs[street_i - 1], r)]  # "Praha 1, Na Příkopě 15, 110 00": a district says more
    if not reads:
        return None
    towns = [x for x in reads if not _region(x[1])] or reads[:1]
    i, _s, (a.city, postcode, warning) = next((x for x in towns if not _district_word(x[1])), towns[-1])
    a.postcode = a.postcode or postcode
    if warning:
        a.warnings.append(warning)
    return i


def _place_name(s: str, r: tuple[str, str, str], c: Country, cs: bool = False) -> bool:
    """The part before the street reads as a place, not a recipient or company ("Jan Jansen", "Société Dupont"): a
    known town or one of its districts ("Praha 1", "København V", "Budapest VI."), a postcode with it ("1067 Dresden"),
    one word ("Getafe", "Kendal"), or a town's shape ("Alcalá de Henares"). `cs`: a city-state's name was written
    after the street, so one word may be the recipient ("Marc, 23 rue ..., Luxembourg") unless it is a commune."""
    if r[1] or r[2] or _district(s) or not cs and len(s.split()) == 1 or fold(s) in _CS_TOWNS.get(c.code, ()):
        return True
    words = s.split()
    n = next((n for n in range(len(words), 0, -1) if (c.code, fold(" ".join(words[:n]))) in _LOCAL), 0)
    if n and (n == len(words) or re.fullmatch(r"(?:\d{1,2}|[IVX]{1,5}|[A-Z]{1,2})\.?", " ".join(words[n:]))):
        return True
    return not cs and _town_shape(words, c)  # "San Sebastián de los Reyes" is not San Sebastián, but a town too


def _town_shape(words: list[str], c: Country) -> bool:
    """'Alcalá de Henares', 'Ústí nad Labem', 'Bergen op Zoom', 'Mülheim an der Ruhr', 'Sesto San Giovanni' (a joiner
    inside), 'Bad Homburg', 'Sankt Pölten', 'La Rochelle', 'Nowy Sącz' (a first word), 'Wiener Neustadt' (a German
    town's ending): a town of several words that no list has. Not 'Jan Jansen', 'Pedro de la Cruz' where people
    are named that way ('de' is no joiner in NL, PT, FR), 'dhr. J. de Vries', 'Société de Transport', 'Café de Paris',
    'St John Ambulance' (an English 'St' town has two words)."""
    joiners, prefixes, endings = _TOWN_SHAPE.get(c.code, ("", "", ""))
    f = [fold(w) for w in words]
    if not 2 <= len(words) <= 5 or not _place_shape(" ".join(words)) or f[0] in _NOT_TOWN_START \
            or any(x in _NAME_PARTICLES for x in f) or re.fullmatch(r"[^\W\d_]\.", words[0]) \
            or any(re.fullmatch(r"[^\W\d_]{1,3}\.", w) for w in words[1:]):
        return False
    for w, x in zip(words, f):  # every word capitalised but the joiners and articles
        if not w[0].isupper() and not (x in _TOWN_ARTICLES or joiners and re.fullmatch(joiners, x)):
            return False
    inside = f[0] not in _GIVEN_NAMES and any(re.fullmatch(joiners, x) and (w.islower() or x in ("san", "santa"))
                                              for w, x in zip(words[1:-1], f[1:-1])) if joiners else False
    return inside or bool(prefixes and re.fullmatch(prefixes, f[0]) and words[0][0].isupper()
                          and not (c.code in ("GB", "IE") and len(words) > 2)) \
        or bool(endings and len(words) == 2 and re.search(rf"(?:{endings})$", f[1]))


def _cut_county(city: str, c: Country) -> tuple[str, str]:
    """'Lucan Co. Dublin' -> ('Lucan', 'Co. Dublin'), 'Kendal Cumbria' -> ('Kendal', 'Cumbria'): a British or Irish
    county glued to the town is not part of its name. Not 'Stratford upon Avon', nor a county alone."""
    words = (city or "").split()
    if c.code not in ("GB", "IE") or len(words) < 2 or _region(city):
        return city, ""
    for k in range(1, len(words)):
        county = " ".join(words[k:])
        if (re.fullmatch(r"(?i)(?:co\.?|county)\s+\S.*", county) or fold(county) in _REGIONS
                or re.fullmatch(r"\w+shire", fold(county))) \
                and fold(words[k - 1]) not in ("upon", "on", "in", "under", "by", "le", "the"):
            return " ".join(words[:k]).rstrip(" ,"), county  # "Kendal, Cumbria" in the city column
    return city, ""


def _district_word(s: str) -> bool:
    # not an Estonian settlement ("Voka alevik, Toila vald"): In-ADS knows it, and the parish written after it may be
    # an older one than the register's
    return fold(s) in _DISTRICT_WORDS


def _not_town(s: str, c: Country | None = None) -> bool:
    """A recipient, company or institution written as its own part ("c/o Müller", "Herr Schmidt", "Firma XY GmbH",
    "Hotel Adlon"); not the Belgian town Spa or "Sas van Gent", nor a Swiss town with its canton ("Baden AG", "Buchs
    AG": Aargau, not a legal form, unless the part has three words or more)."""
    f = fold(s)
    legal = re.search(rf"\S\s+(?:{_LEGAL})$", f)
    if legal and c and c.code in ("CH", "LI") and f.endswith(" ag") and len(f.split()) <= 2 \
            and f.split()[0] not in _AG_COMPANY:
        legal = None
    return bool(_RECIPIENT_RE.match(s) or legal or re.search(rf"\b(?:{_ORG})\b", f))


def _read_town(s: str, c: Country, later: bool = False) -> tuple[str, str, str] | None:
    """A part read as the town: "Milano", "Praha 2", "Dublin 2", "Paris 11e", "1067 Dresden" (postcode missing its
    leading zero) -> (town, postcode, warning); None when it is not a town. `later`: a part after this one could be
    the town, so "Tor 3" is not a district."""
    if not re.search(r"[^\W\d_]", s) or is_unit_line(s) or re.fullmatch(r"[IVX]+\.?", s):  # "III": a Dutch floor
        return None
    if c.code == "PT" and (m := re.fullmatch(r"(\d{4})(?:[\s-]?(\d{3}))?\s+(\D{2,})", s)):
        # "1100-053" written "1100053" / "1100 053", or only its first four digits ("1100 Lisboa")
        if m.group(2):
            return m.group(3).strip(), f"{m.group(1)}-{m.group(2)}", ""
        return m.group(3).strip(), "", f"postcode '{m.group(1)}' is missing its last three digits; the town is used"
    if m := re.fullmatch(r"(?:[A-Z]{1,3}-)?(\d{1,6})\s+(\D{2,})", s):
        if padded := _pad(m.group(1), m.group(2), c):
            return m.group(2).strip(), padded, f"postcode '{m.group(1)}' is missing its leading zeros; read as {padded}"
    if m := re.fullmatch(r"(\D{2,})\s+(\d{1,6})", s):  # "Wien 1010" / "Riga 1050"
        if re.fullmatch(c.postcode, m.group(2)):
            return m.group(1).strip(), m.group(2), ""
    if re.search(r"\d", s) and not (re.fullmatch(r"\D{2,}\s+(?:\d{1,2}(?:e|er|eme|ème)|[A-Z]{1,2}\d[A-Z\d]?)", s)
                                    or re.fullmatch(r"\D{2,}\s+\d{1,2}\.?", s) and (_district(s) or not later)):
        return None  # numbers that are neither a district ("Praha 2", "Dublin 2", "Paris 11e") nor an outward code
    return s, "", ""


def _finish(a: IntlAddress, c: Country, removed: list[str]) -> IntlAddress:
    if not a.city and (cs := city_state(removed)):  # "42 Place Guillaume II, Luxembourg": the city is the country
        a.city = cs
    a.city, county = _cut_county(a.city, c)
    if county:
        a.extra.append(county)
    if m := re.search(r"(?i)\s*\b(?:cedex(?:\s*\d{1,2})?|sp)$", a.city or ""):  # "75509 Paris Cedex 15"
        if c.code in ("FR", "MC") and m.start() > 0:
            a.city, a.cedex = a.city[: m.start()].strip(), True
    if a.po_box and not a.number:
        a.warnings.append(f"'{a.po_box}' is a PO box / parcel locker, not a street address")
    elif a.po_box:
        a.extra.append(a.po_box)
    if not a.number and not a.po_box:
        a.warnings.append("the address has no house number" if a.no_number or a.street else "no house number found in the address")
    return a


def parse_structured(c: Country, street: str, city: str = "", postcode: str = "", unit: str = "") -> IntlAddress:
    """Separate columns. The street column can still hold more than the street ("Vesterbrogade 10, 3. th.",
    "c/o Müller, Hauptstraße 12", "Calle Mayor, 5"), and Address 1 / Address 2 columns put a flat or building first
    ("Flat 3" + "10 Downing Street")."""
    a = IntlAddress(raw=", ".join(p for p in (prep(street), prep(unit), prep(postcode), prep(city)) if p), country=c.code)
    parts = []
    for s in [s.strip() for s in _normalize(street).split(",") if s.strip()]:
        s, junk = _clean(s, c.code)
        a.extra += junk
        if s:
            parts.append(s)
    for s in [s for s in parts if po_box(s, c)]:  # "Postfach 1234" in the street column: a mailbox, as in parse_line
        a.po_box = a.po_box or s
        parts.remove(s)
    u = _clean(prep(unit))[0]
    if u and not is_unit_line(u) and re.search(r"[^\W\d_]", u) and re.search(r"\d", u) and _pick_street([u], c) is not None \
            and _pick_street(parts, c) is None:
        parts, u = [u, *parts], ""  # Address 2 holds the street: "Flat 3" / "10 Downing Street"
    parts = _merge_numbers(parts, c, True)
    i = _pick_street(parts, c)
    if i is None and u and _number_only(u, c) and parts:  # the number in its own column: "Hauptstraße" + "12"
        parts[0], u = f"{parts[0]} {u}" if not c.number_first else f"{u} {parts[0]}", ""
        i = _pick_street(parts, c)
    if i is not None:
        a.street, a.number, a.unit, rest = _split(parts[i], c)
        if re.search(r"[^\W_]", rest):
            a.extra.append(rest)
    elif parts:
        i = 0
        a.street = parts[0]
        if _NO_NUMBER_RE.search(a.street):
            a.street, a.no_number = _NO_NUMBER_RE.sub("", a.street).strip(" ,-"), True
    for j, s in enumerate(parts):
        if j == i:
            continue
        if is_unit_line(s):
            a.unit = ", ".join(p for p in (a.unit, s) if p)
        elif po_box(s, c):
            a.po_box = a.po_box or s
        else:
            a.extra.append(s)
    if u:
        a.unit = ", ".join(p for p in (a.unit, u) if p)
    a.city = prep(city)
    pc = prep(postcode)
    if pc:
        m = _pc_regex(c).search(pc)
        if m:
            a.postcode = m.group("pc").upper()
        elif re.fullmatch(r"\d{1,6}", pc) and (padded := _pad(pc, a.city or "x", c)):
            a.postcode = padded
            a.warnings.append(f"postcode '{pc}' is missing its leading zeros; read as {padded}")
        else:
            a.postcode = pc.upper()
    return _finish(a, c, [])


def city_matches(want: str, have: str | None) -> bool | None:
    if not want or not have:
        return None
    w, h = fold(want), fold(have)
    return w == h or w in h or h in w
