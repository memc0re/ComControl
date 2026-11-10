# Commander-PRO-Protokoll (eigener Treiber)

Dieses Dokument beschreibt den Teil des HID-Protokolls des Corsair iCUE Commander PRO (USB `1b1c:0c10`), den der eigene Treiber in `backend/src/comcontrol/commander/` verwendet. Fremde Treiber (liquidctl, Linux `corsair-cpro`) dienten nur als Protokollreferenz; es wurde kein Code übernommen. Jede Angabe ist mit ihrem Nachweisstand gekennzeichnet:

- **beobachtet** – am realen Gerät mit dem Werkzeug `commander-probe` geprüft,
- **Referenz** – nur aus Fremdquellen übernommen, am Gerät noch nicht geprüft.

Der Nachweis erfolgte an einem teilbestückten Aufbau (ein PWM-Lüfter, ein Fühler). DC-Lüfter, weitere Kanäle und Fühler sind am Gerät **nicht geprüft**.

## Transport

| Eigenschaft | Wert | Stand |
|---|---|---|
| Schnittstelle | Linux `hidraw`, Gerät wird über den physischen USB-Pfad (z. B. `1-4.2`) aus sysfs ermittelt | beobachtet |
| Anfrage | Output-Report, 64 Byte: Befehlsbyte, Argumente, Rest mit `0x00` aufgefüllt. Beim Schreiben auf `hidraw` wird ein führendes Report-ID-Byte `0x00` vorangestellt (65 Byte geschrieben). | beobachtet |
| Antwort | Input-Report, 16 Byte. Byte 0 = Status, ab Byte 1 Nutzdaten | beobachtet |
| Zuordnung | Die Antwort enthält **kein Befehlsecho**. Zuordnung erfolgt allein über die Reihenfolge: genau eine Anfrage, dann genau eine Antwort. Vor jeder Anfrage werden liegengebliebene Reports verworfen; Zugriffe müssen serialisiert sein. | beobachtet |
| Zeitlimit | Treiber wartet höchstens 1 s auf eine Antwort, sonst Fehler | Treiberfestlegung |

### Statusbyte

| Wert | Bedeutung | Stand |
|---|---|---|
| `0x00` | OK | beobachtet |
| `0x01` | ungültiger Befehl | Referenz |
| `0x10` | ungültige Argumente | Referenz |
| `0x11` | Fühler nicht angeschlossen | Referenz |
| `0x12` | Lüfter auf diese Weise nicht steuerbar | Referenz |

Jeder Status ungleich `0x00` ist ein Fehler. Eine Antwort mit falscher Länge ist ungültig und gilt nie als Erfolg.

## Befehls-Whitelist

Alle Mehrbyte-Werte sind Big Endian. Lüfter (0–5) und Fühler (0–3) werden im Protokoll **0-basiert** übertragen; die Beschriftung am Gerät und `commander-probe` zählen ab 1. Andere Befehle (insbesondere RGB, Firmware-/Bootloader-Schreiben) kann der Treiber nicht erzeugen.

### Lesen

| Befehl | Argumente | Antwort (ab Byte 1) | Stand |
|---|---|---|---|
| `0x02` Firmware | – | 3 Byte: Major, Minor, Patch | beobachtet |
| `0x06` Bootloader | – | 2 Byte: Major, Minor | beobachtet |
| `0x10` Fühler angeschlossen | – | 4 Byte, je Fühler `1` = verbunden, `0` = nicht | beobachtet |
| `0x11` Temperatur | Fühler | `s16`, 1/100 °C | beobachtet |
| `0x12` Spannung | Schiene (0 = +12 V, 1 = +5 V, 2 = +3,3 V) | `u16`, mV | beobachtet (+3,3 V siehe unten) |
| `0x20` Lüfter-Betriebsarten | – | 6 Byte: `0` = nicht verbunden, `1` = DC, `2` = PWM | beobachtet (nur PWM und „nicht verbunden“) |
| `0x21` Drehzahl | Lüfter | `u16`, RPM | beobachtet |

Hinweise:

- Ein Temperaturwert von 0 beweist allein keinen fehlenden Fühler; maßgeblich ist zusätzlich `0x10`. Nicht angeschlossene Fühler und Lüfter werden vom Treiber nicht als 0 °C / 0 RPM gewertet.
- Die +3,3-V-Schiene kann deutlich unter 3,3 V lesen (beobachtet: einige 100 mV), vermutlich weil sie am Gerät nicht beschaltet ist. Das ist kein Gerätefehler.

### Schreiben

| Befehl | Argumente | Stand |
|---|---|---|
| `0x23` feste Leistung | Lüfter, Prozent (0–100) | beobachtet (0 %, 50 %, 100 %) |
| `0x25` Hardwarekurve | Lüfter, Fühler, 6 × Temperatur (`u16`, 1/100 °C), 6 × Drehzahl (`u16`, RPM) | beobachtet |
| `0x28` Betriebsart setzen | `0x02`, Lüfter, Betriebsart (`0`/`1`/`2`) | Referenz, **nicht geprüft** |

Eine Schreibantwort enthält nur das Statusbyte. Der Controller bestätigt **keinen** Sollwert und bietet kein Rücklesen von Leistung oder Kurve. Ein gesendeter Befehl gilt daher nur als „übertragen“, nicht als „bestätigt“; Wirkung zeigt sich allein an der gemessenen Drehzahl.

## Kurvenverhalten

| Beobachtung | Stand |
|---|---|
| Zwischen zwei Punkten interpoliert der Controller linear (Abweichung bei ruhender Temperatur typisch unter 10 RPM) | beobachtet |
| Unterhalb des ersten Punkts gilt die Drehzahl des ersten Punkts | beobachtet |
| Oberhalb des letzten Punkts gilt die Drehzahl des letzten Punkts, keine Überschreitung | beobachtet |
| Kurve mit weniger als 6 Punkten, aufgefüllt durch Wiederholung des letzten Punkts, wird angenommen | beobachtet |
| Kurve läuft weiter, nachdem das schreibende Programm beendet ist | beobachtet |
| Wechsel der Steuerart: Controller ändert die Drehzahl nicht sprunghaft, sondern rampt (ca. 10 s) | beobachtet |
| Kurve folgt einer Temperaturänderung innerhalb des Kurvenbereichs in beide Richtungen; bei schneller Änderung hinkt die Drehzahl einige Sekunden nach | beobachtet |
| Verhalten bei während des Betriebs abgezogenem Fühler | **nicht geprüft** |

Auffüllung: Der Treiber verlangt streng aufsteigende Temperaturen. Wiederholte Punkte erhalten daher die Drehzahl des letzten Punkts und eine um je 0,01 °C höhere Temperatur. Die so entstehende effektive Kurve ist mit der eingegebenen funktional gleich, da oberhalb des letzten Punkts ohnehin dessen Drehzahl gilt.
