# Trading-App — Erstinstallation

Diese Anleitung erklärt Schritt für Schritt, wie du die Trading-App auf einem neuen Windows-PC installierst und startest.

---

## Was du brauchst

- Windows 10 oder 11
- Internetverbindung (zwingend erforderlich — die App lädt Kursdaten von Yahoo Finance)
- Ca. 30–60 Minuten Zeit (davon ~20 Min. für den Daten-Download)

---

## Schritt 1 — Python installieren

1. Öffne deinen Browser und gehe auf:
   **https://www.python.org/downloads/**

2. Klicke auf den großen gelben Button **„Download Python 3.11.x"**

3. Starte die heruntergeladene Datei (`python-3.11.x-amd64.exe`)

4. **WICHTIG:** Aktiviere ganz unten das Häkchen **„Add python.exe to PATH"**  
   *(ohne dieses Häkchen funktioniert die Installation nicht!)*

5. Klicke auf **„Install Now"** und warte bis die Installation abgeschlossen ist

6. Klicke auf **„Close"**

---

## Schritt 2 — Code herunterladen

1. Öffne deinen Browser und gehe auf:
   **https://github.com/online-junkie/trading**

2. Klicke auf den grünen Button **„Code"**

3. Klicke auf **„Download ZIP"**

4. Entpacke die ZIP-Datei an einen Ort deiner Wahl, z.B. `C:\Trading`

5. Öffne den entpackten Ordner — du siehst darin einen Unterordner **„Trading"**  
   Wechsle in diesen Unterordner (dort liegt die Datei `asset_analyzer.py`)

---

## Schritt 3 — Installation ausführen

1. Halte die **Shift-Taste** gedrückt und klicke mit der **rechten Maustaste** in den Ordner (auf eine leere Stelle)

2. Wähle **„PowerShell-Fenster hier öffnen"**  
   *(oder: „In Terminal öffnen")*

3. Gib folgenden Befehl ein und drücke Enter:

   ```
   Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process
   ```

4. Gib dann folgenden Befehl ein und drücke Enter:

   ```
   .\install.ps1
   ```

5. Das Skript startet jetzt und arbeitet automatisch. Du siehst laufende Meldungen in blauer, grüner und grauer Schrift.  
   **Bitte warte, das kann einige Minuten dauern.**

---

## Schritt 4 — Admin-Benutzer anlegen

Das Skript fragt dich nach einem Benutzernamen und Passwort für die App:

```
Admin-Benutzername (Standard: admin):
```
→ Drücke einfach Enter um „admin" zu übernehmen, oder tippe einen eigenen Namen

```
E-Mail-Adresse (Standard: admin@localhost):
```
→ Drücke Enter oder gib deine E-Mail-Adresse ein

```
Passwort:
Passwort bestätigen:
```
→ Tippe ein Passwort (mindestens 8 Zeichen), das Passwort wird **nicht angezeigt** — das ist normal

Am Ende siehst du:

```
===========================================
 Installation abgeschlossen!
===========================================
```

---

## Schritt 5 — Daten laden (Pflicht!)

> ⚠️ **Ohne diesen Schritt zeigt die App keine Daten an.**

Die App benötigt Ticker-Stammdaten von Yahoo Finance. Gib im PowerShell-Fenster ein:

```
.\install.ps1 -InitInfo
```

Das Script lädt Metadaten (Name, Sektor, Branche) für alle ~130 vordefinierten Ticker.  
**Bitte warte — das dauert ca. 10–20 Minuten.** Du siehst laufende Ausgaben wie `ADS.DE`, `SAP.DE` usw.

Einige Ticker können mit `404 Not Found` fehlschlagen — das ist normal, Yahoo Finance kennt nicht jeden Ticker. Der Rest wird trotzdem geladen.

Am Ende erscheint wieder:
```
===========================================
 Installation abgeschlossen!
===========================================
```

---

## Schritt 6 — App starten

Doppelklicke auf die Datei **`start.bat`** im Trading-Ordner.

Ein schwarzes Fenster öffnet sich kurz, danach startet automatisch dein Browser mit der Trading-App.

> Falls der Browser nicht automatisch öffnet: Gib manuell `http://localhost:8080` in die Adressleiste ein.

---

## App beenden

Schließe das schwarze PowerShell/Terminal-Fenster, das beim Start geöffnet wurde.

---

## Übersicht der Installationsschritte

| Schritt | Befehl | Pflicht? | Dauer |
|---|---|---|---|
| Umgebung + Config anlegen | `.\install.ps1` | ✅ Ja | ~5 Min |
| Ticker-Stammdaten laden | `.\install.ps1 -InitInfo` | ✅ Ja | ~20 Min |
| Historische Kursdaten laden | `.\install.ps1 -Init` | Optional | Stunden |

---

## Häufige Probleme

### „python wird nicht erkannt" oder „py wird nicht erkannt"
Python wurde ohne PATH-Eintrag installiert. Lösung:
1. Python deinstallieren (Windows-Einstellungen → Apps)
2. Schritt 1 wiederholen — diesmal **unbedingt** das Häkchen „Add python.exe to PATH" setzen

### „running scripts is disabled on this system"
Du hast Schritt 3.3 übersprungen. Führe zuerst diesen Befehl aus:
```
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process
```
Danach nochmal `.\install.ps1`

### Die App öffnet sich, aber ich komme nicht rein
Du hast Benutzername und Passwort aus Schritt 4 verwendet? Groß-/Kleinschreibung beachten.

### „Port already in use" oder die App startet nicht
Ein anderes Programm blockiert Port 8501. Starte den PC neu und versuche es erneut.

---

## Ab dem nächsten Mal

Für jeden weiteren Start reicht ein Doppelklick auf **`start.bat`**.  
Die Installation (Schritte 1–5) muss nur einmal gemacht werden.
