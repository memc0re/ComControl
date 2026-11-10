# ComControl

Web-Oberfläche (Deutsch) zur Überwachung und Steuerung der Lüfter eines Serverschranks über einen per USB angeschlossenen **Corsair iCUE Commander PRO**.

> [!WARNING]
> **Nutzung auf eigene Gefahr.** ComControl greift direkt in die Lüftersteuerung und damit in die Kühlung von Serverkomponenten ein. Fehlerhafte Einstellungen, Softwarefehler, ein Ausfall des Backends, der USB-Verbindung oder des Controllers können zu unzureichender Kühlung, Überhitzung und Hardwareschäden führen. Es gibt in dieser Version **keinen unabhängigen Wächter**, der bei einem Ausfall sicher auf volle Lüfterleistung umschaltet.
>
> Die Software wird **ohne jegliche Gewährleistung** bereitgestellt, insbesondere ohne Gewähr für Funktionsfähigkeit, Eignung für einen bestimmten Zweck oder für Schäden an Hardware, Daten oder sonstigem Eigentum (siehe auch Abschnitte 15 und 16 der GPLv3). Überwache Temperaturen deines Systems unabhängig von ComControl.
>
> ComControl ist kein Produkt von Corsair und steht in keiner Verbindung zu Corsair.

## Projektstand

ComControl befindet sich in Entwicklung und ist **noch nicht einsatzbereit**. Es gibt noch keine Web-Oberfläche und kein Backend.

Vorhanden ist bisher:

- eine Protokollschicht für den Commander PRO in Python (nur Standardbibliothek, direkter Zugriff über Linux `hidraw`),
- das Diagnosewerkzeug `commander-probe` zum Lesen und testweisen Setzen von Werten,
- eine Beschreibung des verwendeten Protokollausschnitts mit Nachweisstand: [`docs/protocol.md`](docs/protocol.md).

Am realen Gerät geprüft sind bisher nur Messwerte, feste Lüfterleistung, Hardwarekurven und das Setzen der Betriebsart PWM an einem PWM-Lüfter. DC-Lüfter und Mehrkanalbetrieb sind **nicht geprüft**.

## Voraussetzungen

- Linux mit `hidraw`-Unterstützung, Python ≥ 3.11
- Schreib-/Lesezugriff auf das `hidraw`-Gerät des Commander PRO (USB `1b1c:0c10`), z. B. über eine udev-Regel, die **nur** dieses Gerät freigibt:

  ```
  SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1b1c", ATTRS{idProduct}=="0c10", GROUP="plugdev", MODE="0660"
  ```

- Das Kernelmodul `corsair_cpro` darf das Gerät nicht gleichzeitig verwenden (z. B. per `blacklist corsair_cpro` in `/etc/modprobe.d/`). Andere Programme, die den Controller ansprechen, müssen beendet sein.

## Diagnosewerkzeug

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

.venv/bin/commander-probe list            # nur sysfs, öffnet kein Gerät
.venv/bin/commander-probe info            # Firmware, Fühler, Lüfter-Betriebsarten
.venv/bin/commander-probe status --watch 5
```

Lüfter und Fühler werden wie am Gerät beschriftet ab 1 gezählt. Befehle, die den Gerätezustand ändern, benötigen `--write`:

```sh
.venv/bin/commander-probe --write set-percent 1 100
.venv/bin/commander-probe --write set-curve 1 1 25:600 35:1000 45:1500
```

Ein gesendeter Befehl wird vom Controller nicht durch Rücklesen bestätigt; maßgeblich ist die gemessene Drehzahl.

Tests (ohne Hardware, gegen einen gefälschten Transport):

```sh
cd backend && .venv/bin/pytest -q
```

## Lizenz

ComControl steht unter der **GNU General Public License v3.0 oder später** (GPL-3.0-or-later), siehe [`LICENSE`](LICENSE).

Der Treiber ist eine eigene Implementierung. Fremde Projekte (u. a. liquidctl und der Linux-Treiber `corsair-cpro`) dienten ausschließlich als Protokollreferenz; es wurde kein Code daraus übernommen.
