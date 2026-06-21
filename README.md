# OpenSquawk Bridge

OpenSquawk Bridge verbindet deinen Flugsimulator mit
[opensquawk.de](https://opensquawk.de). Die App läuft als kleines Desktop-Fenster,
streamt Telemetrie an OpenSquawk und öffnet die Push-to-talk-Funkseite für ATC.

Die App kann direkt aus dem Quellcode gestartet werden. Für normale Nutzer baust
du daraus eine anklickbare Windows-`.exe` oder macOS-`.app`.

## Was die App kann

- OpenSquawk-Konto per Pairing-Code mit diesem Gerät verbinden.
- Push-to-talk-Funkseite auf dem PC öffnen oder per QR-Code auf Handy/iPad nutzen.
- Globalen Push-to-talk-Trigger setzen: Tastatur, Tastenkombination oder
  Joystick/HOTAS-Button.
- Simulatorquelle wählen:
  - `Dummy flight` zum Testen ohne Simulator.
  - `MSFS 2024`, wenn der Simulator läuft.
  - `MSFS 2020`, wenn der Simulator läuft.
  - `X-Plane` und `FlightGear` sind vorbereitet, aber noch nicht aktiv.
- Live-Status und Telemetrie anzeigen, inklusive Flugphase, Funkfrequenzen,
  Squawk, Position, Geschwindigkeit und Höhe.
- Flight actions anlegen: Ketten aus Wartezeiten, Tastendrücken und Klicks, die
  per App-Start, Sim-Erkennung, Aircraft-Erkennung, GPS-Sprung, Hotkey oder
  Joystick-Button ausgelöst werden.
- Optional mit dem Betriebssystem starten, damit die Bridge nach dem Einloggen
  automatisch bereit ist.

## Voraussetzungen

- Python 3.10 oder neuer.
- Git, wenn du das Repository klonen möchtest.
- Windows, macOS oder Linux.

Zusätzlich braucht die Oberfläche eine Webview-Runtime:

- Windows: Microsoft Edge WebView2 Runtime. Auf Windows 11 ist sie normalerweise
  vorhanden. Auf Windows 10 kann sie fehlen; die App zeigt dann beim Start einen
  Hinweis und öffnet den Download.
- macOS: WKWebView ist im System enthalten.
- Linux: WebKitGTK/PyGObject, zum Beispiel auf Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-webkit2-4.1
```

## Aus dem Quellcode starten

Im Projektordner:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bridge_app.py
```

Auf Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bridge_app.py
```

Für lokale Backend-Tests kannst du die Ziel-URL ändern:

```bash
OPENSQUAWK_BASE_URL=http://localhost:3000 python bridge_app.py
```

## Anklickbare App bauen

Der Build wird immer für das Betriebssystem erstellt, auf dem du den Befehl
ausführst. Eine Windows-`.exe` muss also auf Windows gebaut werden, eine
macOS-`.app` auf macOS.

### macOS oder Linux

```bash
./build.sh
```

Oder direkt:

```bash
python3 build.py
```

### Windows

`build.bat` doppelklicken oder in der Eingabeaufforderung ausführen:

```bat
build.bat
```

Oder direkt:

```bat
python build.py
```

Das Build-Skript installiert die nötigen Build-Werkzeuge, bündelt die Dateien aus
`web/`, erzeugt das passende Icon und startet PyInstaller.

## Wo liegt die fertige App?

Nach dem Build findest du alles im Ordner `dist/`:

| Betriebssystem | Datei/Ordner | Start |
| --- | --- | --- |
| Windows | `dist\OpenSquawk Bridge.exe` | Datei doppelklicken |
| macOS | `dist/OpenSquawk Bridge.app` | App doppelklicken oder nach `Programme` ziehen |
| Linux | `dist/OpenSquawk Bridge/` | Programmdatei im Ordner starten |

Die Builds sind aktuell nicht signiert:

- Windows kann SmartScreen anzeigen. Dann `Weitere Informationen` und `Trotzdem
  ausführen` wählen.
- macOS kann Gatekeeper blockieren. Dann einmal Rechtsklick auf die `.app`,
  `Öffnen` und erneut `Öffnen` wählen.

## Einrichtung in der App

1. OpenSquawk Bridge starten.
2. `Open login in browser` anklicken.
3. Im Browser bei opensquawk.de anmelden und den angezeigten Pairing-Code
   verbinden.
4. Zur App zurückkehren. Nach erfolgreicher Verbindung erscheint die Hauptansicht.
5. Unter `Simulator` die Quelle wählen:
   - Zum Testen `Dummy flight`.
   - Für Microsoft Flight Simulator den passenden laufenden Simulator wählen.
6. Optional unter `System` den Schalter `Start with operating system` aktivieren.
   Die App richtet dann den Autostart für dein Betriebssystem ein.
7. Unter `Live ATC` die Funkseite auf diesem PC öffnen oder den QR-Code mit einem
   zweiten Gerät scannen.
8. Optional unter `Push-to-talk hotkey` eine Taste, Tastenkombination oder einen
   Joystick-Button belegen.

Die lokale Konfiguration liegt in:

```text
~/.opensquawk-bridge/config.json
```

Beim Logout vergisst die App die lokale Verbindung und erzeugt einen neuen
Pairing-Code.

## Projektstruktur

```text
bridge_app.py          Desktop-App, API, Autostart, HTTP, Hintergrundthreads
msfs_source.py         MSFS-2020/2024-Erkennung und SimConnect-Telemetrie
simulator.py           Dummy-Flug zum Testen ohne Simulator
actions.py             Flight-action-Ketten, Trigger und Ausführung
web/index.html         Oberfläche
web/style.css          Styling
web/app.js             Frontend-Logik
build.py               Build-Skript für .exe/.app/Linux-Bundle
build.bat              Windows-Build
build.sh               macOS/Linux-Build
tests/                 Tests
```

## Tests

```bash
python -m pytest
```
