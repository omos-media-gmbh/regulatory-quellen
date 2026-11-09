# Regulatory-Quellen – täglicher Abgleich

Teil des Regulatory Agent (MetaFlow, Pilot). Lädt jeden Morgen den **Volltext** der Rechtsgrundlagen für die Claim-Prüfung, vergleicht ihn mit dem Vortag und zeigt Änderungen auf. Durchsucht außerdem Rechtsprechungs-Feeds nach Stichwörtern.

Nur öffentliche Rechtstexte und öffentliche Gerichtsmeldungen. Keine internen Daten, keine Unterlassungserklärungen, keine Spezifikationen.

| Ordner/Datei | Inhalt |
|---|---|
| `quellen.yml` | Welche Texte und Feeds geprüft werden, Stichwörter |
| `texte/` | aktueller Volltext je Quelle (Git-Verlauf = alle früheren Fassungen) |
| `stand.json` | Fassung/Stand, Prüfdatum, letzte Fehler je Quelle |
| `aenderungen/` | eine Datei je erkannter Änderung mit den geänderten Passagen; `index.json` als Liste |
| `funde/index.json` | neue Gerichtsentscheidungen aus den Feeds, die ein Stichwort enthalten |

Quellen der Texte: EU-Recht über das Amt für Veröffentlichungen (Cellar/EUR-Lex), neueste konsolidierte Fassung. Deutsches Recht über gesetze-im-internet.de.

Ob eine Änderung für unsere Werbung relevant ist, bewertet Regulatory. Der Regulatory Agent fasst Änderungen nur zusammen.
