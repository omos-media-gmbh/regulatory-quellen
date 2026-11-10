"""Täglicher Check der Rechtsgrundlagen für den Regulatory Agent.

1. Lädt den Volltext jeder Quelle aus quellen.yml (EU: neueste konsolidierte Fassung über Cellar,
   DE: Gesamtausgabe bei gesetze-im-internet.de) und speichert ihn als Text unter texte/.
2. Vergleicht mit dem bisherigen Stand. Bei Änderung entsteht aenderungen/<datum>-<id>.md mit
   den geänderten Passagen und ein Eintrag in aenderungen/index.json.
3. Liest Rechtsprechungs-Feeds, filtert nach Stichwörtern und ergänzt funde/index.json.

Keine Bewertung, ob eine Änderung relevant ist – das macht Regulatory.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
TEXTE = ROOT / "texte"
AEND = ROOT / "aenderungen"
FUNDE = ROOT / "funde"
STAND = ROOT / "stand.json"
UA = {"User-Agent": "MetaFlow-Regulatory-Monitor/1.0 (taeglicher Abgleich oeffentlicher Rechtstexte)"}
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
HEUTE = dt.date.today().isoformat()


def get(url: str, versuche: int = 3, timeout: int = 90, **kw) -> requests.Response:
    last = None
    headers = {**UA, **kw.pop("headers", {})}
    for versuch in range(versuche):
        try:
            r = requests.get(url, headers=headers, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (versuch + 1))
    raise RuntimeError(f"Abruf fehlgeschlagen: {url}: {last}")


def html_to_text(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    zeilen = [re.sub(r"[ \t ]+", " ", z).strip() for z in text.splitlines()]
    out, leer = [], False
    for z in zeilen:
        if not z:
            if not leer:
                out.append("")
            leer = True
            continue
        out.append(z)
        leer = False
    return "\n".join(out).strip() + "\n"


# ---------- EU ----------
def neueste_konsolidierung(celex: str) -> tuple[str, str | None]:
    q = f"""PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?celex ?date WHERE {{
 ?act cdm:resource_legal_id_celex "{celex}"^^xsd:string .
 ?cons cdm:act_consolidated_consolidates_resource_legal ?act .
 ?cons cdm:resource_legal_id_celex ?celex .
 OPTIONAL {{ ?cons cdm:act_consolidated_date ?date . }}
  FILTER(STRSTARTS(STR(?celex), "0{celex[1:]}-"))
}} ORDER BY DESC(?celex) LIMIT 1"""
    r = get(f"{SPARQL}?query={quote(q)}&format=application%2Fsparql-results%2Bjson")
    b = r.json()["results"]["bindings"]
    if not b:
        return celex, None  # nie geändert -> Originalfassung
    return b[0]["celex"]["value"], b[0].get("date", {}).get("value")


HTML_ACCEPT = {"Accept": "application/xhtml+xml,text/html;q=0.9,*/*;q=0.1", "Accept-Language": "de"}


def cellar_dokument(url: str) -> str:
    """Lädt eine Cellar-Fassung. Cellar liefert bei mehrteiligen Texten erst eine Liste (DOC_1, DOC_2 …)."""
    r = get(url, headers=HTML_ACCEPT)
    t = html_to_text(r.content)
    if len(t) > 2000:
        return t
    soup = BeautifulSoup(r.content, "html.parser")
    teile = []
    for a in soup.find_all("a", href=True):
        h = a["href"].replace("http://", "https://")
        if re.search(r"/DOC_\d+$", h) and h not in teile:
            teile.append(h)
    if not teile:
        raise RuntimeError(f"{url}: zu kurz ({len(t)} Zeichen): {t[:200]!r}")
    texte = [html_to_text(get(h, headers=HTML_ACCEPT).content) for h in sorted(teile, key=lambda x: int(x.rsplit("_", 1)[1]))]
    return "\n".join(texte)


def eu_text(celex: str, basis: str) -> str:
    kandidaten = [celex] + ([basis] if basis != celex else [])
    fehler = []
    for c in kandidaten:
        for u in (f"https://publications.europa.eu/resource/celex/{c}.DEU.xhtml",
                  f"https://publications.europa.eu/resource/celex/{c}.DEU.html"):
            try:
                t = cellar_dokument(u)
                if len(t) > 2000:
                    return t
                fehler.append(f"{u}: zu kurz")
            except Exception as e:  # noqa: BLE001
                fehler.append(str(e)[:300])
    raise RuntimeError(" | ".join(fehler))


# ---------- DE ----------
NEURIS = "https://testphase.rechtsinformationen.bund.de"


def de_text(q: dict) -> tuple[str, str | None, str]:
    """Erst gesetze-im-internet.de, sonst Rechtsinformationsportal des Bundes (NeuRIS, Testphase)."""
    fehler = []
    if q.get("abk"):
        try:
            j = get(f"{NEURIS}/v1/legislation?searchTerm={quote(q['abk'])}&size=20", versuche=2, timeout=30).json()
            treffer = [m["item"] for m in j.get("member", []) if m["item"].get("abbreviation") == q["abk"]]
            if not treffer:
                raise RuntimeError(f"NeuRIS: {q['abk']} nicht gefunden")
            item = sorted(treffer, key=lambda i: i["legislationIdentifier"])[-1]
            html = next(e["contentUrl"] for e in item["encoding"] if e["encodingFormat"] == "text/html")
            t = html_to_text(get(NEURIS + html, versuche=2, timeout=30).content)
            fassung = item["legislationIdentifier"].split("/")[5]
            return t, f"Fassung vom {fassung} (Rechtsinformationsportal)", NEURIS + html
        except Exception as e:  # noqa: BLE001
            fehler.append(str(e)[:200])
    if q.get("url"):
        try:
            r = get(q["url"], versuche=2, timeout=25)
            t = html_to_text(r.content)
            m = re.search(r"Zuletzt geändert durch[^\n]{4,200}", t) or re.search(r"Stand:\s*([^\n]{4,200})", t)
            stand = re.sub(r"\s+", " ", m.group(0)).strip() if m else None
            return t, stand, q["url"]
        except Exception as e:  # noqa: BLE001
            fehler.append(str(e)[:200])
    raise RuntimeError(" | ".join(fehler))


# ---------- Vergleich ----------
def diff_md(name: str, alt: str, neu: str, alt_stand: str, neu_stand: str) -> tuple[str, int, int]:
    a, b = alt.splitlines(), neu.splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    teile, plus, minus = [], 0, 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        kontext = next((a[k] for k in range(i1 - 1, -1, -1) if re.match(r"^(Artikel|Art\.|§|Anhang|ANHANG)\s*\S+", a[k])), "")
        weg = [z for z in a[i1:i2] if z.strip()]
        dazu = [z for z in b[j1:j2] if z.strip()]
        minus += len(weg)
        plus += len(dazu)
        block = [f"### {kontext or 'Stelle ' + str(i1 + 1)}"]
        block += [f"- ~~{z}~~" for z in weg[:40]]
        block += [f"+ **{z}**" for z in dazu[:40]]
        if len(weg) > 40 or len(dazu) > 40:
            block.append("_(gekürzt)_")
        teile.append("\n".join(block))
    kopf = (f"# Änderung: {name}\n\nErkannt am {HEUTE}\n\nVorher: {alt_stand}\nJetzt: {neu_stand}\n\n"
            f"{minus} Zeilen entfernt, {plus} Zeilen neu. Inhaltliche Bewertung: Regulatory.\n\n")
    return kopf + "\n\n".join(teile[:120]) + "\n", plus, minus


def lade_json(p: Path, default):
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def schreibe_json(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def pruefe_quellen(cfg) -> list[dict]:
    stand = lade_json(STAND, {})
    index = lade_json(AEND / "index.json", [])
    neu_eintraege, fehler = [], []
    jobs = [("eu", q) for q in cfg.get("eu", [])] + [("de", q) for q in cfg.get("de", [])]
    for typ, q in jobs:
        qid, name = q["id"], q["name"]
        if q.get("manuell"):
            stand[qid] = {"name": name, "manuell": q["manuell"], "geprueft": None}
            continue
        try:
            if typ == "eu":
                celex, datum = neueste_konsolidierung(q["celex"])
                text = eu_text(celex, q["celex"])
                fassung = f"konsolidiert {datum}" if datum else "Originalfassung"
                quelle = f"https://eur-lex.europa.eu/legal-content/DE/TXT/?uri=CELEX:{celex}"
            else:
                text, fassung, quelle = de_text(q)
                fassung = fassung or "Stand nicht erkannt"
                celex = None
        except Exception as e:  # noqa: BLE001
            fehler.append(f"{name}: {e}")
            stand.setdefault(qid, {})["letzterFehler"] = {"am": HEUTE, "meldung": str(e)[:300]}
            continue
        h = hashlib.sha256(text.encode()).hexdigest()
        datei = TEXTE / f"{qid}.txt"
        alt = stand.get(qid, {})
        def kennung(c, url):
            return (c or "")[1:11] if c else re.sub(r"^https?://([^/]+).*$", r"\1", url or "")
        gleiche_quelle = kennung(alt.get("celex"), alt.get("quelle")) == kennung(celex, quelle)
        if datei.exists() and alt.get("hash") and alt["hash"] != h and gleiche_quelle:
            md, plus, minus = diff_md(name, datei.read_text("utf-8"), text, alt.get("fassung", "?"), fassung)
            if plus + minus > 0:
                pfad = AEND / f"{HEUTE}-{qid}.md"
                pfad.write_text(md, "utf-8")
                e = {"datum": HEUTE, "id": qid, "name": name, "vorher": alt.get("fassung"), "jetzt": fassung,
                     "zeilenNeu": plus, "zeilenWeg": minus, "datei": f"aenderungen/{pfad.name}", "quelle": quelle}
                index.insert(0, e)
                neu_eintraege.append(e)
        datei.write_text(text, "utf-8")
        stand[qid] = {"name": name, "fassung": fassung, "celex": celex, "quelle": quelle, "hash": h,
                      "zeichen": len(text), "geprueft": HEUTE}
    stand["_lauf"] = {"am": HEUTE, "fehler": fehler}
    schreibe_json(STAND, stand)
    schreibe_json(AEND / "index.json", index)
    if fehler:
        print("FEHLER:\n" + "\n".join(fehler), file=sys.stderr)
    return neu_eintraege


def pruefe_feeds(cfg) -> list[dict]:
    worte = [w.lower() for w in cfg.get("stichwoerter", [])]
    funde = lade_json(FUNDE / "index.json", [])
    bekannt = {f["link"] for f in funde}
    neu = []
    for f in cfg.get("feeds", []):
        try:
            r = get(f["url"])
            root = ET.fromstring(r.content)
        except Exception as e:  # noqa: BLE001
            print(f"Feed {f['name']}: {e}", file=sys.stderr)
            continue
        for item in root.iter("item"):
            titel = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            besch = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ").strip()
            if not link or link in bekannt:
                continue
            treffer = [w for w in worte if w in (titel + " " + besch).lower()]
            if not treffer:
                continue
            e = {"gefunden": HEUTE, "quelle": f["name"], "titel": titel, "link": link,
                 "beschreibung": besch[:1500], "stichwoerter": treffer[:8]}
            funde.insert(0, e)
            bekannt.add(link)
            neu.append(e)
    schreibe_json(FUNDE / "index.json", funde[:1000])
    return neu


def main() -> int:
    cfg = yaml.safe_load((ROOT / "quellen.yml").read_text("utf-8"))
    for d in (TEXTE, AEND, FUNDE):
        d.mkdir(exist_ok=True)
    aenderungen = pruefe_quellen(cfg)
    funde = pruefe_feeds(cfg)
    zusammenfassung = {"am": HEUTE, "aenderungen": aenderungen, "funde": len(funde)}
    (ROOT / "letzter-lauf.json").write_text(json.dumps(zusammenfassung, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(zusammenfassung, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
