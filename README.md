# WowWalker

Geh auf dem Walking Pad, und deine Figur in **World of Warcraft: Forever** geht mit.
Bleibst du stehen, bleibt auch deine Figur stehen.

```
iPhone in der Hosentasche ──WLAN──▶ WowWalker auf dem PC ──▶ hält "W" gedrückt, solange du gehst
   (App phyphox)                    (erkennt deine Schritte)
```

- Kein Mac, kein Xcode, kein Apple-Developer-Account nötig
- Das Walking Pad braucht kein Bluetooth oder WLAN
- Nur Python, keine weiteren Programme oder Pakete

Deine Apple Watch kannst du nebenbei ganz normal mit dem Training **„Gehen (Indoor)“**
laufen lassen. So landen Kalorien und Puls wie gewohnt in Apple Health.

---

## Was du brauchst

- Einen **Windows-PC** mit **Python 3.8 oder neuer**
- Ein **iPhone** mit der kostenlosen App **[phyphox](https://phyphox.org)**
- PC und iPhone im **selben WLAN**

## Einrichtung (einmalig)

1. **Python installieren:** Lade es von [python.org](https://www.python.org/downloads/) herunter.
   Setz im Installer unbedingt den Haken bei **„Add python.exe to PATH“**.
2. **WowWalker herunterladen:** Klick auf GitHub auf **Code → Download ZIP** und entpack
   die Datei in einen Ordner deiner Wahl.
3. **phyphox vorbereiten:**
   1. Öffne in phyphox das Experiment **„Beschleunigung (ohne g)“**.
   2. Tippe oben rechts auf **⋮** und wähle **„Fernzugriff erlauben“**.
      Unten erscheint eine Adresse wie `http://192.168.178.42:8080`.
   3. Stell in den iPhone-Einstellungen unter **Anzeige & Helligkeit → Automatische Sperre**
      die Option **„Nie“** ein. phyphox muss im Vordergrund bleiben.
   4. Optional: Schalte unter **Bedienungshilfen → Geführter Zugriff** den geführten Zugriff
      ein und starte ihn in phyphox mit dreimaligem Drücken der Seitentaste. Dann tippt die
      Hosentasche nichts versehentlich an.
4. **Kalibrieren:** Doppelklick auf **`kalibrieren.bat`**. Gib beim ersten Mal die Adresse
   aus phyphox ein. Steck das iPhone in die Hosentasche, geh ein paar Schritte und stell dich
   dann auf die Seitenleisten. Mehr dazu unter [Kalibrieren](#kalibrieren).
5. **Loslegen:** Doppelklick auf **`start.bat`**, dann WoW starten.

Die Adresse wird in `wowwalker.json` gespeichert. Ändert sie sich (zum Beispiel nach
einem Router-Neustart), startest du einmal mit `start.bat --neue-adresse`.

## Benutzung

| Du ...                                  | Deine Figur ...                               |
|-----------------------------------------|-----------------------------------------------|
| gehst auf dem Band                      | läuft vorwärts (W wird gedrückt gehalten)     |
| stellst dich auf die Seitenleisten      | bleibt nach etwa 1 Sekunde stehen             |
| drückst die **Pause-Taste**             | bleibt stehen, auch wenn du weitergehst       |
| drückst die Pause-Taste noch einmal     | läuft wieder mit                              |
| wechselst aus WoW in ein anderes Fenster| bleibt stehen (es wird nichts in andere Programme getippt) |

- **Pause-Taste:** Standard ist **Maustaste 5**, also die vordere Seitentaste der Maus.
  So kannst du zum Kämpfen, Looten oder beim Händler weitergehen, während deine Figur
  steht. Ein hoher Piepton bedeutet aktiv, ein tiefer pausiert. Die Taste lässt sich in
  den Einstellungen ändern (`pause_key`). Leg sie in WoW auf nichts anderes.
- **Lenken, Springen, Rückwärts:** Das geht ganz normal mit Maus und Tastatur.
- **Chatten:** Drück vorher die Pause-Taste. Drückst du beim Gehen selbst W (zum Beispiel
  im Chat), kämpft WowWalker nicht dagegen an. Es übernimmt erst wieder, wenn du kurz
  stehen bleibst oder zweimal die Pause-Taste drückst.
- **Beenden:** Drück **Strg+C** oder schließ das Fenster. Die Taste wird dabei immer
  losgelassen. Das passiert auch automatisch, wenn die Verbindung zum iPhone abreißt.

## Kalibrieren

`kalibrieren.bat` drückt keine Tasten. Es zeigt zweimal pro Sekunde den stärksten
Ausschlag an:

```
Ausschlag   0.31 m/s²  [###          |                          ]  stehen
Ausschlag   3.44 m/s²  [###############################         ]  GEHEN
```

Der Strich `|` markiert die Schwelle (`threshold`, Standard **1.5 m/s²**).

- Beim **Gehen** sollte der Balken deutlich über dem Strich liegen.
- Beim **Stehen auf den Seitenleisten**, während das Band weiterläuft, sollte er deutlich
  darunter liegen.

In deiner ersten Testmessung lag Gehen bei etwa 2–4 m/s² und Stehen unter 0,5 m/s². Der
Standardwert sollte also passen. Wenn du auf dem Pad sehr langsam gehst und der Balken
kaum über den Strich kommt, senk `threshold`, zum Beispiel auf `1.0`.

## Einstellungen (`wowwalker.json`)

Die Datei wird beim ersten Start neben `wowwalker.py` angelegt. Du kannst sie mit dem
Editor öffnen und ändern. Danach musst du WowWalker neu starten.

| Einstellung            | Standard              | Bedeutung |
|------------------------|-----------------------|-----------|
| `phyphox_url`          | –                     | Adresse aus phyphox |
| `move_key`             | `"w"`                 | Taste, die beim Gehen gedrückt gehalten wird (z.B. `"e"` oder `"up"`) |
| `pause_key`            | `"mouse5"`            | Pause/Weiter: `mouse3` (Mausrad-Klick), `mouse4`, `mouse5`, `f1`–`f24`, `pause`, `scrolllock`, `numpad0`–`numpad9`, Buchstaben … |
| `window_title`         | `"World of Warcraft"` | Taste nur drücken, wenn ein Fenster mit diesem Titel vorne ist (`""` = immer) |
| `threshold`            | `1.5`                 | m/s²: ab diesem Ausschlag zählt es als Bewegung |
| `events_to_start`      | `3`                   | So viele Ausschläge ... |
| `start_window`         | `2.0`                 | ... innerhalb so vieler Sekunden bedeuten: du gehst los |
| `stop_after`           | `1.0`                 | Nach so vielen Sekunden ohne Ausschlag stehst du |
| `min_event_gap`        | `0.3`                 | Höchstens ein Ausschlag pro so vielen Sekunden |
| `stale_after`          | `1.0`                 | Nach so vielen Sekunden ohne Daten vom iPhone wird die Taste losgelassen |
| `poll_interval`        | `0.05`                | Sekunden zwischen zwei Abfragen beim iPhone |
| `buffer_reset_minutes` | `10`                  | Messspeicher in phyphox alle X Minuten leeren, möglichst beim Stehen, damit das iPhone nicht langsamer wird (`0` = nie) |
| `autostart`            | `true`                | Messung in phyphox automatisch starten |
| `beep`                 | `true`                | Piepton bei Pause/Weiter |

## Probleme und Lösungen

| Problem | Lösung |
|---------|--------|
| „Keine Verbindung zum iPhone“ | Sind beide Geräte im selben WLAN? Gast-WLANs blockieren oft die Verbindung zwischen Geräten. Ist phyphox offen, der Fernzugriff an und der Bildschirm an? Hat sich die Adresse geändert? Dann `start.bat --neue-adresse`. |
| „Keine neuen Messwerte“ | Der iPhone-Bildschirm ist ausgegangen oder phyphox ist im Hintergrund. Stell die Auto-Sperre auf „Nie“. |
| Es steht „Gehen - Taste gedrückt“, aber die Figur läuft nicht | Läuft WoW als Administrator? Dann musst du auch `start.bat` mit Rechtsklick → „Als Administrator ausführen“ starten. |
| „WoW ist nicht im Vordergrund“, obwohl WoW offen ist | Die Meldung zeigt den Titel des aktiven Fensters. Trag ihn (oder einen Teil davon) bei `window_title` ein. |
| Die Figur stockt beim langsamen Gehen | Erhöh `stop_after` (zum Beispiel `1.5`) oder senk `threshold`. Prüf das mit `kalibrieren.bat`. |
| Die Figur läuft kurz los, wenn du das Handy anfasst oder dich bewegst | Erhöh `events_to_start` (zum Beispiel `4`) oder `threshold`. |
| „python“ oder „py“ wird nicht erkannt | Installier Python neu und setz den Haken bei „Add python.exe to PATH“. |
| W scheint nach einem Absturz zu „hängen“ | Drück einmal kurz W auf der Tastatur. |

## Hinweis zu Blizzard

WowWalker macht nur eins: Es hält die Lauf-Taste gedrückt, solange du dich wirklich
bewegst. Das ist vergleichbar mit einem Controller oder Fußpedal, also eine Eingabe für
eine Aktion. Es liest nichts aus dem Spiel aus und automatisiert nichts anderes. Eine
offizielle Freigabe von Blizzard gibt es dafür aber nicht. Die Nutzung erfolgt auf eigene
Verantwortung.

## Für Entwickler

- Alles steckt in `wowwalker.py`, das nur die Python-Standardbibliothek nutzt.
- Unter Linux und macOS läuft das Programm automatisch im Trockenlauf und drückt keine Tasten.
- Tests: `python -m unittest -v`. Sie enthalten einen Nachbau des phyphox-Fernzugriffs
  und künstliche Geh- und Steh-Signale.
- Gelesen wird über die phyphox-Schnittstelle
  `GET /get?acc_time=T&accX=T|acc_time&accY=T|acc_time&accZ=T|acc_time`.
  Sie liefert nur Werte, die neuer als der Zeitpunkt `T` sind. Gesteuert wird mit
  `GET /control?cmd=start|clear`.
