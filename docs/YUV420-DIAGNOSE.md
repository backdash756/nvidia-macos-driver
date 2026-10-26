# Diagnose von 4K/60 über DP zu HDMI

Diese Diagnose vergleicht zwei getrennte NVKMS-Modusvalidierungen. Sie soll zeigen, ob ein 3840×2160-Modus um 60 Hz mit den bisherigen Parametern scheitert und mit `preferYUV420=true` besteht. Sie ist kein Fix für die Ausgabe und kein Nachweis eines erfolgreichen Display-Modesets.

Quellbasis des Branches ist der vorhandene Forkstand `6e375469` (Changelog 2.6.2). Die untersuchten Displayquellen einschließlich `os-nvkms-xnu.cpp` sind dort gegenüber dem lokalen 2.5.4-Stand `4677905` unverändert. Es wurde kein Treiberpaket gebaut und keine Binärversion auf 2.5.4 umetikettiert.

## Umfang und Dateien

- `kexts/NVRM/patches/615-yuv420-diagnostic.patch` ergänzt die Diagnose in den externen NVIDIA-Quellen `kapi/src/nvkms-kapi.c` und `src/nvkms-modepool.c` des Tags **615.78.08**.
- `kexts/NVRM/os-nvkms-xnu.cpp` ergänzt den Opt-in-Hook `nvkms_yuv420_diag_claim()`. Er beschränkt die Diagnose auf ausgewählte Display-IDs und beansprucht jede ID höchstens einmal pro Boot.
- `tools/test_yuv420_diagnostic.py` prüft den Diagnosepfad unter Linux. Die tatsächlichen Testergebnisse sind dem Ausführungsprotokoll zu entnehmen.

Ohne das Bootargument `nvyuv420diag` werden die zusätzlichen A/B-Abfragen nicht gestartet. Die Standardparameter der produktiven Modusabfrage bleiben unverändert. Auch mit aktiviertem Argument ersetzt die Diagnose weder deren Antwort noch die produktive Modusliste.

## Ablauf

Sobald `GetDisplayMode()` für eine ausgewählte ID erstmals einen Kandidaten mit 3840×2160 und 59–61 Hz zurückliefert, startet die Diagnose:

1. Durchlauf **A** beginnt bei Modusindex 0 mit den bestehenden Standardparametern.
2. Nach dessen Ende beginnt Durchlauf **B** erneut bei Modusindex 0. Ausschließlich `preferYUV420` ist zusätzlich gesetzt.
3. Während jedes Durchlaufs bleiben die Auswahlparameter gleich. Beide Durchläufe verwenden eigene, frisch initialisierte Anfragen.
4. Für passende 4K-Kandidaten werden der interne Modus und eine lokale Hin-und-zurück-Konvertierung durch KAPI protokolliert. Die konvertierten Kopien gelangen nicht in einen Modeset.

Es werden ausschließlich `VALIDATE_MODE_INDEX`-Abfragen ergänzt. Es gibt keinen zusätzlichen Modeset- oder Commit-Aufruf und keine Abschaltung von Sicherheits- oder Bandbreitenprüfungen. NVKMS-Validierung kann trotzdem interne Caches und Hardwareabfragen verwenden; die Diagnose ist kein Beweis vollständig nebenwirkungsfreier Hardwarezugriffe. Zusätzliche Abfragen und Logging können den Start verlängern.

Pro Durchlauf gilt eine Obergrenze von 512 Abfragen. Erst ein von NVKMS gemeldetes Listenende führt zu `COMPLETE`. Eine ausgeschöpfte Obergrenze beziehungsweise ein Abfragefehler führt zu `INCOMPLETE`; Allokations- und IOCTL-Fehler werden als `ABORT` gemeldet. Ein unvollständiger Durchlauf darf nicht als vollständiger Vergleich ausgewertet werden.

Die Kandidatenliste kann von den Validierungsparametern abhängen. **A und B daher über Quelle, vollständige Raster-/Sync-Timings, Pixeltakt und gegebenenfalls VIC zuordnen, nicht allein über denselben Modusindex.** Während der Erfassung weder Kabel noch Eingänge wechseln. Der Patch erzwingt keine unveränderliche EDID-/Hotplug-Generation über beide Durchläufe; ein solcher Wechsel macht den Vergleich erneut prüfbedürftig.

## Anwenden auf den NVIDIA-Quellbaum

Voraussetzung ist ein zum vorhandenen NullMoth-Build passender externer NVIDIA-Baum für **615.78.08**. Die nachfolgenden Variablen müssen auf die tatsächlichen lokalen Verzeichnisse zeigen:

```bash
driver_repo=/pfad/zum/nvidia-macos-driver
ogkm_root=/pfad/zum/open-gpu-kernel-modules
diag_patch="$driver_repo/kexts/NVRM/patches/615-yuv420-diagnostic.patch"

git -C "$ogkm_root" apply --check --directory=src/nvidia-modeset "$diag_patch"
git -C "$ogkm_root" apply --directory=src/nvidia-modeset "$diag_patch"
```

Diese Befehle werden vom Root des vollständigen NVIDIA-Repositories aus ausgewertet. Vorhandene lokale Änderungen und die bestehende NullMoth-Patchfolge müssen erhalten bleiben. Bei einem fehlgeschlagenen `--check` die Abweichung untersuchen; nicht mit erzwungener Anwendung oder durch Entfernen bestehender Patches fortfahren.

Die Diagnose ist gegen unveränderte 615.78.08-Quellen und zusätzlich nach `615-viewport-scaling.patch` geprüft. Das bestätigt nicht die vollständige historische Patchfolge: `610-display-completion.patch` lässt sich mit seinen vorhandenen Kontexten nicht vollständig auf unverändertes 615 anwenden. Der zum bisherigen Treiber gehörende, bereits funktionierende 615-Buildbaum ist deshalb maßgeblich; die alten 610-Patches hier nicht blind neu aufsetzen.

## Bauen und Installieren unter macOS

Der Diagnosepatch und der neue XNU-Hook müssen zusammen gebaut werden. Im vorhandenen funktionierenden Darwin-/Xcode-Buildsetup sind mindestens diese Teile neu zu erstellen:

- NVIDIA-Objekt für `kapi/src/nvkms-kapi.c`;
- NVIDIA-Objekt für `src/nvkms-modepool.c` und das zugehörige Modeset-Archiv;
- NullMoths `os-nvkms-xnu.cpp` und das dagegen gelinkte `NVRM.kext`.

Die zusammengehörigen Komponenten über den bestehenden Build- und Installationsprozess bereitstellen. Ein Vendorarchiv allein enthält den neuen XNU-Hook nicht. Beim Linken auf ungelöste Symbole achten und das erzeugte Kext-Bundle mit den üblichen Build-/Signaturprüfungen kontrollieren.

**`kexts/NVRM/build-completion-archive.py` ist hierfür ungeeignet.** Das vorhandene Skript ist auf NVIDIA 610.57.04, bestimmte vorhandene Darwin-Objekte und eine feste 610-Versionskennung zugeschnitten. Es ist kein vollständiger 615-Builder. `build/accel_build.sh` baut NVAccel und ersetzt ebenfalls keinen NVRM-Build.

Das Repository stellt keinen vollständigen eigenständig ausführbaren 615-NVRM-Build bereit. Ein bereits funktionierendes macOS-/Xcode-Buildsetup für den verwendeten NullMoth-Treiber bleibt Voraussetzung. Keine Kernelcollection-, Freigabe- oder Signaturprüfungen umgehen; den vorhandenen Installationsprozess für den passenden Kext-Satz verwenden. Vorher das bekannte funktionierende Paket und dessen Wiederherstellungsweg bereithalten.

## Aktivieren und Logs erfassen

Die Diagnose unterstützt zunächst **eine GPU**. Ziel-IDs sind die `display 0x...`-Werte aus deren aktuellen NVRMFB-Logs. Sie sind keine dauerhaft stabilen Portnummern. Nur einzelne Bit-IDs sind unterstützt; dynamische oder anders codierte IDs werden nicht allgemein abgedeckt.

Im bisherigen Mitschnitt entsprachen die betroffenen Wege den IDs `0x400` und `0x4000`; ihre kombinierte Maske ist `0x4400`. Nur wenn diese Zuordnung weiterhin stimmt, lautet das zusätzliche Bootargument:

```text
nvyuv420diag=0x4400
```

Es ist über den vorhandenen OpenCore-Konfigurationsweg zusätzlich zu den bisherigen Argumenten einzutragen. Andere Bootargumente unverändert lassen. Nach dem Boot die Zuordnung anhand der Enumeration erneut prüfen. Die Beanspruchung einer ID gilt bootweit und ist nicht nach GPU getrennt; die Maske deshalb nicht ungeprüft auf Mehr-GPU-Systemen einsetzen. Für eine Wiederholung ist ein neuer Boot erforderlich, auch nach einem Diagnoseabbruch.

Logs über das vorhandene Diagnoseverfahren oder lesend im Terminal erfassen:

```bash
log show --last 30m --style compact \
  --predicate 'eventMessage CONTAINS "Y420-AB"'
```

Zusätzlich die NVRMFB-Enumeration aufbewahren, damit `dpy=0x...` dem physischen Signalweg zugeordnet werden kann. Ohne `BEGIN`-Zeilen ist nicht bewiesen, dass die Diagnose lief. Mögliche Gründe sind ein fehlendes Argument, ein nicht passender Build, eine falsche Ziel-ID oder kein abgefragter Kandidat im Filterbereich.

## Ausgabe interpretieren

Jede Ausgabe trägt `Y420-AB`, die Display-ID und bei den Durchläufen `pass=A` beziehungsweise `pass=B`.

| Feld | Bedeutung |
|---|---|
| `source`, `index`, Raster-/Syncwerte | Identität des Kandidaten; Indizes zwischen A und B nicht ungeprüft gleichsetzen. |
| `pclkHz` | Pixeltakt des vollständigen Modus in Hz. Bei typischem 4K/60 bleibt er auch für 4:2:0 bei 594 MHz. |
| `rrMilliHz` | Bildfrequenz in Tausendstel Hz, zum Beispiel 60000 für 60 Hz. |
| `masks rgb`, `y420` | EDID-abgeleitete BPC-Bitmasken des konkreten Timings, keine direkt ablesbare Zahl von Bits pro Komponente. |
| `vic` | CTA-Videocode, soweit dem Kandidaten zugeordnet. |
| `selected` | Ausgewähltes Enum: `0=NONE`, `1=SW`, `2=HW`. |
| `hdmi`, `dp` | Von NVKMS verwendete HDMI-/DP-Bibliothekszuordnung; keine vollständige physische Adapteridentifikation. |
| `maxPclkKHz`, `singleLinkKHz` | Von NVKMS verwendete effektive Pixelclock-/Single-Link-Grenzen in kHz. Interpretation ist vom Protokoll und Format abhängig; keine elektrische TMDS-Messung. Rohe RM-Dongle- oder aktive PCON-DPCD-Fähigkeiten werden nicht separat abgefragt. |
| `final format` | Ausgangsformat: `0=RGB`, `1=YCbCr422`, `2=YCbCr444`, `3=YCbCr420`. |
| `bpc` | Echte Bits pro Farbkomponente: `6`, `8`, `10`, `12`; `0` bedeutet unbekannt. |
| `range` | NVKMS-Enumwert für den Quantisierungsbereich: `0=FULL`, `1=LIMITED`. |
| `valid` | Ergebnis der betreffenden internen Validierung. |
| `yuv420`, `roundtrip`, `lost` | Internes Enum vor der KAPI-Konvertierung, Enum nach lokalem Rückweg und Kennzeichen einer Abweichung. |
| `info:` | Vorhandener NVKMS-Validierungstext, ergänzt um die Diagnosewerte. |

Der Info-Puffer ist auf `NVKMS_MODE_VALIDATION_MAX_INFO_STRING_LENGTH` begrenzt, im untersuchten Stand 2048 Byte. Die Ausgabe wird in kurze Zeilen aufgeteilt. `INFO_MAY_BE_TRUNCATED` bedeutet, dass Text am Pufferende fehlen kann; ein fehlender Ablehnungsgrund darf dann nicht als Erfolg interpretiert werden. Scheitert die Validierung vor der Farbwahl, ist keine `final format`-/BPC-Zeile zu erwarten. Auch das ist kein Beweis einer bestimmten Farbtiefe.

Für typische CTA-Timings benötigt 4K/60 mit RGB und 8 BPC 594 MHz TMDS-Zeichenrate; mit 4:2:0 und 8 BPC 297 MHz. **Der vollständige 594-MHz-Pixeltakt darf bei diesem Vergleich nicht mit der TMDS-Zeichenrate verwechselt oder einfach halbiert werden.**

| Vergleich | Aussage |
|---|---|
| A: `NONE`, `valid=0`; B: `SW`/`HW`, `valid=1` | Die frühe 4:2:0-Auswahl ist entscheidend für die interne Validierung. Ein funktionierender Display-Modeset ist damit noch nicht bewiesen. |
| B bleibt `NONE`, obwohl `y420` Unterstützung enthält | Anschlusszweig und zusätzliche 4:2:0-Zulässigkeitsbedingungen prüfen. |
| Geparste `y420`-Maske enthält keine Unterstützung | EDID-Übernahme und Zuordnung des konkreten Timings untersuchen. |
| B wählt 4:2:0, bleibt aber ungültig | Der `infoString` muss die verbleibende Prüfung erklären. |
| `yuv420=SW`/`HW`, `roundtrip=NONE`, `lost=1` | Die lokale KAPI-Hin-und-zurück-Konvertierung verliert das Kennzeichen. Das ist kein tatsächlich ausgeführter Modeset. |

## Linux-Prüfungen und offene Validierung

Der vorgesehene Testaufruf für den vollständigen externen NVIDIA-Quellbaum lautet:

```bash
cd "$driver_repo"
python3 tools/test_yuv420_diagnostic.py --vendor "$ogkm_root"
```

Unter Linux bestanden zwölf Testfälle mit einem simulierten NVKMS-Endpunkt: A/B-Trennung mit unterschiedlichen Kandidatenindizes, unveränderte produktive Antworten, Filter-/Ende-/Fehlerfälle, beide Allokationsfehler, Abfrage- und Puffergrenzen, HW-/SW-KAPI-Kennzeichenverlust und der nebenläufige Einmal-Hook. Patch-Anwendung und Rücknahme wurden ohne Fuzz geprüft; die übergebenen NVIDIA-Originaldateien blieben unverändert.

Zusätzlich prüft derselbe Testaufruf beide vollständigen gepatchten NVIDIA-C-Dateien mit `-fsyntax-only`, echten 615-Headern und Include-/Define-Optionen aus dem Vendor-Makefile. Beide Syntax- und Formatprüfungen bestanden unter Linux mit GCC. Das ist keine Darwin-Kompilation, kein Linktest und kein Test des EDID-Parsers oder der Hardwarevalidierung; deren Ergebnisse simuliert der Test-Endpunkt.

Erst mit dem passenden macOS-/Xcode-Build sind Link- und Kext-Kompatibilität prüfbar. Erst auf echter Hardware sind Startverhalten, A/B-Ergebnisse, Adaptergrenzen, Logvollständigkeit und unveränderte Displayausgabe prüfbar. Diese Punkte bleiben bis zur entsprechenden Ausführung offen. Die Diagnose stellt keine 4K/60-Ausgabe um und behebt das Problem noch nicht.

## Rücknahme

1. Für zukünftige Starts das zusätzliche Bootargument entfernen. Das unterbindet neue A/B-Abfragen, ersetzt aber die eingebaute Diagnosebinärdatei nicht durch die vorherige Version.
2. Den Vendorpatch nur dann rückwärts anwenden, wenn die Prüfung ohne Konflikte gelingt:

   ```bash
   git -C "$ogkm_root" apply --reverse --check --directory=src/nvidia-modeset "$diag_patch"
   git -C "$ogkm_root" apply --reverse --directory=src/nvidia-modeset "$diag_patch"
   ```

3. Die Änderungen im NullMoth-Branch nach Prüfung mit `git diff` gezielt zurücknehmen. Solange sie uncommittet sind, nur die zu dieser Diagnose gehörenden Änderungen wiederherstellen beziehungsweise neuen Dateien entfernen; bestehende eigene Änderungen nicht pauschal überschreiben. Nach einem späteren Commit ist ein gezieltes `git revert <Diagnose-Commit>` eine nachvollziehbare Alternative.
4. Auf dem Mac das bekannte funktionierende Treiberpaket über den bestehenden Installations- und Kernelcollection-Prozess wiederherstellen und neu starten. Ein Branchwechsel oder rückwärts angewandter Quellpatch allein verändert bereits installierte Kexts nicht.

Der Diagnosebranch muss weder gemergt noch veröffentlicht werden. Ein Pull Request zum ursprünglichen NullMoth-Projekt ist für diese lokale Untersuchung nicht erforderlich.
