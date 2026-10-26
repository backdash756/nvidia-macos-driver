# NVRM-Diagnosebuild für Tahoe 26.7.1 / x86_64

## Ergebnis und Status

Aus dem vorliegenden öffentlichen Fork und unveränderten NVIDIA-Quellen lässt sich derzeit **kein vollständiger, zum ausgelieferten 2.5.4 nachweislich kompatibler NVRM-Build reproduzieren**. Die XNU-Portierungsquellen sind vorhanden, aber das vollständige Darwin-Build-/Linkrezept und die exakte Vorbereitung des ausgelieferten NVIDIA-Unterbaus fehlen. Ein erfundener `make TARGET_OS=Darwin`-Befehl würde diese Lücke nicht schließen.

Der kleinste sinnvolle Diagnosebuild ist dagegen klar abgrenzbar: einen bereits funktionierenden, zu 2.5.4 passenden Darwin-Build einschließlich der unveränderten NVRM-Einzelobjekte und des finalen Linkrezepts weiterverwenden, zwei NVKMS-Objekte und den XNU-Hook neu bauen, anschließend genau **NVRM.kext** neu linken. Die übrigen funktionierenden 2.5.4-Komponenten bleiben unverändert. Das ist ein bedingter Buildplan, noch keine geprüfte Bauanleitung bis zur fertigen Kext.

Diese Untersuchung hat nichts installiert, keine Kernel Collection erzeugt und keine Systemdateien, EFI-/NVRAM-Einstellungen oder Sicherheitskonfigurationen verändert. Es wurde kein macOS-Build ausgeführt. Die folgenden späteren Testschritte sind keine bereits ausgeführten Aktionen.

## Quellen und reproduzierbare Ausgangsdaten

| Bestandteil | Festgelegter beziehungsweise festzustellender Stand |
|---|---|
| Installiertes System | Laut Terminalausgabe Tahoe 26.7.1, Build `25G241`, x86_64; am Testsystem erneut prüfen. |
| Referenztreiber | Tatsächlich funktionierendes vollständiges 2.5.4-Paket und dessen installierte Dateien einschließlich Signaturen/Metadaten. |
| Lokaler 2.5.4-Quellvergleich | Commit `4677905`; dieser Quellstand allein beweist nicht die vollständige Provenienz der Releasebinärdatei. |
| Forkbasis | `6e375469` (Changelog 2.6.2). |
| Diagnoseänderung | Commit `ff37f3812fe170610885081f3a9cb223eb580938`; siehe [YUV420-DIAGNOSE.md](YUV420-DIAGNOSE.md). |
| NVIDIA | Tag `615.78.08`; die öffentliche Git-Abfrage ergab Tagobjekt `4bfe30bb78e7364959e59c16cfefd88e8c14670e` und aufgelösten Commit `1a82fbfda8f321b056eb3568850f9316c5e465c5`. Lokalen Baum und sämtliche zusätzlichen Patches erfassen. |
| Firmware | Vorhandene, zum Treiber gehörende GSP-Firmware 615.78.08 beibehalten; kein Firmwareumbau für diese Diagnose. |

Zur Reproduzierbarkeit gehören ein Manifest mit allen Quell-SHAs, Patchreihenfolge und Patchhashes, Compiler-/SDK-/Linkerversionen, vollständigen Kommandos samt Arbeitsverzeichnissen, Objekt-/Archivmanifesten und SHA-256-Werten der Eingaben und Ausgabe. Der erste Maßstab ist ein funktional kompatibler Build. Byteidentität zum Release ist ohne dessen vollständige Provenienz und deterministische Metadaten-/Signierregeln nicht zugesichert.

## Vorhandene und fehlende Buildteile

| Befund | Konkreter Beleg und Konsequenz |
|---|---|
| XNU-Port ist vorhanden | `kexts/NVRM/NVRM.cpp`, `os-xnu.cpp`, `os-xnu2.cpp`, `os-nvkms-xnu.cpp`, Power-/GPUVA-/Surfshare-Module, RM-interne C-Brücken und `nvkms_shaders.cpp`. Es fehlt nicht pauschal der gesamte macOS-Port. |
| NVRM-Bootstrap fehlt | `kexts/NVRM/rmcc.py:6–7` erwähnt `build-nvrm.sh`, das im Checkout und der untersuchten vorhandenen Git-Historie nicht gefunden wurde. Der Helfer erwartet bereits `src/nvidia/_out/Darwin_x86_64/compile_cmds.sh`. |
| Modeset-Archivhelfer ist kein 615-Bootstrap | `kexts/NVRM/build-completion-archive.py:14–15` erwartet vorhandene Darwin-Compile-Kommandos und `libnvkms.a`; Zeile 33 schreibt ausdrücklich eine 610.57.04-Kennung. Nicht für diese Diagnose unverändert verwenden. |
| Öffentliche NVIDIA-Makefiles sind kein fertiger Kext-Build | Die 615-Ziele erzeugen `nv-kernel.o` und `nv-modeset-kernel.o`. Ihre Regeln enthalten GNU-Linkerskripte, `objcopy` und ELF-orientierte Verarbeitung. Sie erzeugen nicht den hier benötigten vollständigen Darwin-Mach-O-/Kext-Build. Plattformmakros für Apple in `cpuopsys.h` ersetzen dieses Rezept nicht. |
| Vorhandene andere Skripte lösen den NVRM-Build nicht | `build/accel_build.sh` und `accel_build_all.sh` bauen NVAccel; `build_agdc.sh` baut NVRMAGDC. Die Translator-/Plugin-/Mesa-Skripte bauen User-Space-Komponenten. |
| Vollständiges 615-Patchmanifest fehlt | Insbesondere müssen die ausgelieferten Completion-/KAPI-Erweiterungen und Viewport-Anpassungen erhalten bleiben. `610-display-completion.patch` ist nicht vollständig auf unverändertes 615 anwendbar. Den Release-Unterbau nicht durch Vanilla 615 ersetzen. |
| Release-Metadatenrezept fehlt | `NVRM/Info.plist` und `KMOD_EXPLICIT_DECL` in `NVRM.cpp:45` enthalten im Quellstand Version `0.1`; geladen wurde 2.5.4. Die konsistente Releasebearbeitung muss bekannt sein. Nicht nur eine Plist-Version erfinden oder den Build als unverändertes Original ausgeben. |

Für einen eigenständigen vollständigen Build fehlen konkret:

1. Die Darwin-Anpassungen und das vollständige Objektmanifest für RM und NVKMS, einschließlich generierter Dateien, Konfigurationsdefines, Shader-/Binäreinbettung und Symbolbehandlung.
2. Die vollständige, geordnete 615-Patchfolge des funktionierenden Releasebaums.
3. Die Erzeugungsregeln für Mach-O-`libnvkernel.a` und `libnvkms.a` oder einen funktionierenden, überprüfbaren Ausgangsbuild dieser Archive.
4. Die Kommandos für sämtliche NullMoth-XNU-Objekte und RM-internen Zusatzobjekte mit den zu den Archiven passenden Typdefinitionen.
5. Das finale NVRM-Linkrezept: Objekte, Archive und deren Reihenfolge, Bibliotheken, Sichtbarkeits-/Exportregeln, Kext-Einstieg und Deployment Target.
6. Die konsistente Erstellung von Bundle-/Kmod-Metadaten, Signatur und Paketmanifest sowie Toolchain-/SDK-Pins.

Ein installiertes `NVRM.kext` lässt sich nicht zuverlässig in diese ursprünglichen Archive, Einzelobjekte und vollständigen Compilerkommandos zurückverwandeln. Ein vollständiges 2.5.4-Paket ist ein wichtiger Referenz- und Rücksetzbestand, aber kein Ersatz für die Buildinputs.

## Toolchain und Abhängigkeiten

Das README nennt **Xcode 16**, jedoch keine konkrete Unterversion, Apple-Clang-Version, SDK-Version oder 615-NVRM-Deployment-Einstellung. Eine bestimmte 16.x-Version ist damit nicht als qualifiziert nachgewiesen. Für den kompatiblen Minimalbuild ist zunächst die Toolchain des funktionierenden Originalbuilds maßgeblich; sie muss danach explizit gepinnt werden.

Benötigt werden Apple Clang/Clang++, Darwin-Linker und Archivwerkzeuge, passende macOS-Kernel-/IOKit-/libkern-Header, Python 3 sowie Mach-O-, Plist- und Signaturprüfwerkzeuge. Der vorhandene NVAccel-Helfer verwendet `xcrun --show-sdk-path` und `Kernel.framework/Headers`; seine Flags sind Hinweise, kein vollständiges NVRM-Rezept. Auch das im 610-Archivhelfer verwendete Deployment Target 15.0 ist kein Beleg für die korrekte Einstellung eines neu rekonstruierten 615-Builds.

Die Erwähnung des 26.7.1-KDK in `gen_tahoe_fwd.py` betrifft die Rekonstruktion von **NVAccel**-Vtables. Daraus folgt keine belegte Pflicht, einen NVRM-only-Diagnosebuild gegen genau dieses KDK zu kompilieren. Ob zusätzliche private Kernelheader oder ein KDK nötig sind, muss das tatsächliche ursprüngliche NVRM-Rezept klären. Kernel-Collection-Erzeugung und C/C++-Kompilation sind getrennte Schritte.

Rust, Meson/Ninja, Mesa/NVK und der Shadertranslator müssen für diesen NVRM-only-Versuch nicht neu gebaut werden. Die vorhandenen funktionierenden User-Space-Komponenten bleiben im Referenzstand. Die Firmwarebilder werden nicht vom Diagnosepatch erzeugt; `os-xnu2.cpp:29` erwartet sie unter `/Library/NullMoth/nvfw/nvidia/615.78.08/`.

Folgende spätere Bestandsaufnahme auf dem Build-Mac ist ausschließlich lesend und benötigt kein `sudo`:

```bash
sw_vers
uname -m
xcodebuild -version
xcrun --sdk macosx --show-sdk-path
xcrun --sdk macosx --show-sdk-version
xcrun clang --version
xcrun --find clang++
xcrun --find ld
xcrun --find ar
```

## Kleinster bedingter Buildablauf

**Technische Voraussetzung vor dem Bauen:** Ein funktionierender 2.5.4-/615-Darwin-Ausgangsbuild und die oben genannten fehlenden Rezepte müssen vorliegen. Solange das nicht der Fall ist, bei Quellprüfung und Dokumentation bleiben. Kein vollständiges Buildskript mit geratenen Flags erzeugen.

Wenn diese Voraussetzung erfüllt ist:

1. Außerhalb von `/Library`, `/System`, EFI und produktiven Buildausgaben einen eigenen Stagingbaum erstellen. Ausgangsarchive und unveränderte Einzelobjekte nur kopieren; Originale und deren Hashes aufbewahren. Den tatsächlich gepatchten 615-Baum verwenden.
2. Diagnosepatch zunächst mit `git apply --check` prüfen; den dokumentierten Patch aus [YUV420-DIAGNOSE.md](YUV420-DIAGNOSE.md) nur im Staging-Quellbaum anwenden. Die bestehende Patchfolge erhalten. Keine losen 610-Patches erzwingen.
3. `kapi/src/nvkms-kapi.c` und `src/nvkms-modepool.c` mit ihren originalen Darwin-Compile-Kommandos neu kompilieren. Includes, Defines, Architektur und ABI-relevante Optionen unverändert übernehmen; nur Quell-, Objekt- und Abhängigkeitsausgaben auf den Stagingbaum umstellen.
4. In einer Kopie des ursprünglichen `libnvkms.a` genau die zugehörigen Archivmitglieder ersetzen und dessen Index mit dem passenden Darwin-Werkzeug aktualisieren. Originale Mitgliedsnamen/Objektformate prüfen; keine ELF-Objekte aus Linux einsetzen.
5. `os-nvkms-xnu.cpp` aus dem Diagnosecommit mit dem ursprünglichen NullMoth-Kommando neu bauen. Sonst bleibt der neue `nvkms_yuv420_diag_claim`-Hook beim finalen Linken unaufgelöst.
6. Das unveränderte, passende `libnvkernel.a` und alle übrigen ursprünglichen NVRM-Objekte weiterverwenden. Mit dem ursprünglichen finalen NVRM-Linkkommando neu linken, Metadaten konsistent erzeugen und das vollständige Kext-Bundle im Stagingbaum signieren und prüfen.
7. Alle Eingabe-/Ausgabehashes und Kommandos archivieren. **Kein Installationsschritt ist Bestandteil dieses Buildablaufs.**

Zur Funktionsprüfung gehört später außerdem ein Baseline-Build **ohne** Diagnosepatch aus denselben Inputs. Wenn bereits dieser nicht gegen die bekannten 2.5.4-Komponenten funktioniert, ist ein Diagnose-A/B-Test des Displays nicht aussagekräftig.

## Binärkompatibilität 2.5.4 gegenüber 2.6.2

Git-Vergleich: `4677905` gegen `6e375469`, also vor dem Diagnosecommit.

| Bestandteil | Änderung | Konsequenz für diesen Versuch |
|---|---|---|
| NVRM-Core/XNU-Port, NVRMFB, NVRMAGDC | Bytegleiche Quellen; NVRM-Unterordner `accel` separat betrachtet. | Keine gefundene Quell-ABI-Änderung erzwingt deren gemeinsamen Neuaufbau. |
| VRAM-/GPUVA-/Flip-ABI und Plist-Vorlagen | Unverändert; Versionen VRAM 2, GPUVA 1, Flip 2. | Diagnosepatch ändert diese Schnittstellen nicht. |
| NVAccel | Drosselung der Loghäufigkeit abgelehnter VRAM-Allokationen. | Kein Grund, das funktionierende Tahoe-NVAccel 2.5.4 für die Diagnose auszutauschen. |
| User-Space-Plugin | Unter anderem Video-/Capture-/ANGLE-Änderungen. | Für die NVRM-Diagnose nicht benötigt; nicht beiläufig aktualisieren. |
| Installer | Unter anderem KextPolicy-Codehashbehandlung und Neustarthinweise. | Installeränderungen nicht mit Binärkompatibilität gleichsetzen und nicht ungeprüft zur Diagnose übernehmen. |

Trotz Quellgleichheit ist kein vollständiger Binärvertrag bewiesen. NVRM veröffentlicht die `nvkms-kapi`-Funktionstabelle als Zeiger; NVRMFB übernimmt sie direkt (`NVRM.cpp:68`, `NVRMFB/fb/nvrm-fb.cpp:2448`). Die vorhandenen Funktionszeigerprüfungen sind kein vollständiger Layout-Fingerprint. Gleiches NVIDIA-Versionslabel oder gleiche sichtbare Bundleversion reicht deshalb nicht.

**NVRM-only ist das Ziel**, sofern das ursprüngliche gepatchte KAPI-Layout und alle konsumierten Exporte erhalten bleiben. NVRMFB, NVAccel für macOS 26, NVRMAGDC, Plugins, NVK, Firmware und die nicht betroffenen Caches bleiben im funktionierenden 2.5.4-Zustand. Die Diagnose fügt keinen öffentlichen KAPI-Struct hinzu. Sie verlangt aber, dass die beiden neuen Vendorobjekte und der XNU-Hook im selben neu gelinkten NVRM enthalten sind.

## Artefaktprüfungen vor jedem Hardwareversuch

Auf Dateien im Staging-/Referenzverzeichnis prüfen, nicht auf produktiven Systempfaden:

```bash
reference_kext=/pfad/zur/gesicherten-2.5.4/NVRM.kext
candidate_kext=/pfad/zum/staging/NVRM.kext
file "$candidate_kext/Contents/MacOS/NVRM"
lipo -archs "$candidate_kext/Contents/MacOS/NVRM"
plutil -lint "$candidate_kext/Contents/Info.plist"
otool -hv "$candidate_kext/Contents/MacOS/NVRM"
otool -l "$candidate_kext/Contents/MacOS/NVRM"
nm -gU "$reference_kext/Contents/MacOS/NVRM"
nm -gU "$candidate_kext/Contents/MacOS/NVRM"
nm -u "$candidate_kext/Contents/MacOS/NVRM"
codesign --verify --strict "$candidate_kext"
dwarfdump --uuid "$candidate_kext/Contents/MacOS/NVRM"
shasum -a 256 "$candidate_kext/Contents/MacOS/NVRM"
```

Architektur x86_64, Kext-Mach-O-Typ, Deployment-/SDK-Metadaten, unveränderte Bundle-ID/Abhängigkeiten und die weiterhin benötigten öffentlichen Symbole vergleichen. Keine zweite konkurrierende NVRM-Instanz durch Umbenennen des Bundles oder seiner ID erzeugen. Die neue Hookfunktion muss im fertigen NVRM intern aufgelöst sein. Eine Kext hat erwartbare externe Kernel-/IOKit-Symbole; `nm -u` muss nicht leer sein. Neue unerklärte Imports und fehlende vorher konsumierte Exporte sind dagegen zu untersuchen. Erfolgreiche Signaturprüfung allein beweist weder Kernel-Kompatibilität noch Ladefreigabe.

## Separates Testsystem: Voraussetzungen, noch keine Installation

Ein Testvolume reduziert das Risiko für Produktionsdateien, macht einen Kernelversuch aber nicht gefahrlos. Beim i9-14900KF steht keine nutzbare integrierte Grafik als Ausweichanzeige bereit. Schwarzer Bildschirm, Kernelpanic, Bootschleife und Verlust ungesicherter Testdaten bleiben möglich.

- Eigenständige **System-/Data-Volumegruppe**, möglichst auf separatem Datenträger, mit exakt passendem Tahoe-Build verwenden. Ein weiteres Datenvolume oder ein geteilter Data-Bestand genügt nicht.
- Zuerst den unveränderten vollständigen 2.5.4-Zustand auf diesem Testsystem qualifizieren. Den unabhängigen Start in die bekannte Produktionsinstallation beziehungsweise einen Rettungsstart vor dem Diagnoseversuch nachweislich erproben.
- Produktions-EFI unverändert lassen. Ein unabhängiger bekannter OpenCore-Rettungsdatenträger ist besser als die Annahme, ein Volumewechsel allein garantiere den Rückweg. Die bereits funktionierenden Sicherheitseinstellungen beibehalten, keine zusätzlichen Prüfungen umgehen.
- EFI/OpenCore und NVRAM sind nicht automatisch volumenisoliert. NullMoth verwendet globale `nullmoth-remove`, `nullmoth-nv-starts` und `nullmoth-nv-ok`-Werte; Diagnose-Bootargumente können ebenfalls fortbestehen. Ein Teststart kann daher den nächsten Produktionsstart beeinflussen.
- Während A/B weder Kabel noch Matrix-/Switch-Eingänge ändern. Der Diagnosepatch garantiert keine identische Anschlussgeneration zwischen den Durchläufen.

Die später benötigte Bereitstellung muss vorab separat geprüft werden: eindeutig identifizierte Test-Volumegruppe, nur dort selektiv NVRM ersetzen, dortige fremde Kexts und 2.5.4-Komponenten erhalten, Auxiliary KC gegen genau deren Boot-/System-KCs bauen und vor Veröffentlichung inspizieren. Finale `_PrelinkBundlePath`-Werte müssen auf permanente Pfade im Testsystem zeigen, nicht auf einen temporären Preflight-Ordner.

**Der vorhandene Standardinstaller ist dafür kein selektives Werkzeug:** `package/install.sh:6–9` ändert mit `--payload` nur die Quelle; Zeilen 11–18 und 449 adressieren `/Library`, `/System` und `--volume-root /`. Der Installer ersetzt alle vier Kexts sowie weitere Komponenten. Ihn nicht auf dem Produktionssystem mit der Erwartung ausführen, dadurch ein anderes Volume oder nur NVRM zu aktualisieren. Auch ein Start des Skripts aus einem anderen Arbeitsverzeichnis ändert sein Ziel nicht. Den Treiber nicht zusätzlich aus `EFI/OC/Kexts` injizieren.

Es wird hier absichtlich kein ungeprüfter `sudo cp`-/`kmutil create`-/Offline-Installationsbefehl bereitgestellt. Ein solcher Schritt ist erst sinnvoll, wenn ein prüfbares Artefakt und ein qualifiziertes Testsystem vorhanden sind.

## Rückweg zum funktionierenden 2.5.4

Vor dem späteren Versuch auf getrenntem, erreichbarem Speicher sichern: vollständiges originales 2.5.4-Paket samt unveränderten Prüfsummen, Original-NVRM, übrige Kexts und deren Metadaten, GPU-Bundles/NVK, Firmware, Accelerator-Caches, Versionsaufzeichnung, passende Auxiliary KC sowie die bekannte EFI-Konfiguration. Dateibackups müssen Signaturen, erweiterte Attribute, Rechte und Links erhalten. Änderungen an einem Paket müssen als eigene Diagnoseartefakte mit eigenem Manifest erkennbar bleiben; originale Prüfsummen nicht umschreiben.

Der bevorzugte Rückweg ist das unangetastete Produktionssystem über seinen bekannten Bootweg. Vor dessen Start berücksichtigen, dass Bootargumente und NullMoth-NVRAM-Zustände global sind. Ein `nullmoth-remove`-Schalter darf nicht als ausschließlich auf das Testvolume wirkende Rettungsfunktion dargestellt werden.

Für die Rücksetzung des Testsystems dessen vollständigen bekannten 2.5.4-Zustand wiederherstellen. Eine alte KC nur bei passendem macOS-Build und passendem übrigen Kextzustand wiederverwenden; andernfalls aus den wiederhergestellten permanenten Dateien neu erzeugen und prüfen. Die selektive Änderung von NVRM am Testsystem benötigt einen ebenso selektiv geprüften Rückweg. Entfernen von `nvyuv420diag` stoppt die zusätzlichen Abfragen, ersetzt aber die Diagnosebinärdatei nicht.

**Wichtige Einschränkung des vorhandenen Uninstallers:** `package/uninstall.sh <Backup>` ist im untersuchten Stand keine Versionswiederherstellung. Das Argument wird in Zeilen 49–50 nur geprüft; anschließend entfernt das Skript die Komponenten und Firmware. Die vom Installer ausgegebene Zeile `Undo: ... uninstall.sh $BK` darf deshalb nicht als Zusicherung einer Wiederherstellung von 2.5.4 benutzt werden. Die automatische Lastgood-Logik ist ein anderer Mechanismus und ersetzt den vorab geprüften Rückweg nicht.

## Bereits geprüft und weiterhin offen

- Geprüft: Git-Unterschiede zwischen 2.5.4 und Forkbasis, vorhandene Buildskripte und ihre vorausgesetzten Eingaben, öffentliche NVIDIA-Buildziele und Auflösung des 615-Tags, Installations-/Rücknahmepfade im Quellcode.
- Aus der Diagnoseentwicklung bereits geprüft: zwölf Linux-Szenarien, Syntax-/Formatprüfung beider vollständiger geänderter NVIDIA-C-Dateien mit echten 615-Headern, Diagnosepatch-Anwendung/-Rücknahme und Anwendung nach dem 615-Viewport-Patch. Das ist kein Mach-O-/Darwin-Build.
- Offen: vollständiger releasepassender Darwin-Ausgangsbuild, Toolchain-/SDK-Pins, finaler Kext-Link und Metadatenerzeugung, Binärvergleich, Ladefähigkeit und Boot auf der RTX 5080.
- Offen: praktisch qualifizierte separate Test-Volumegruppe und ihr selektiver Bereitstellungs-/Rücksetzprozess. Hier wurde nichts davon eingerichtet oder installiert.

Der nächste notwendige Input ist daher ein **vollständiger 615-Darwin-Buildbaum beziehungsweise dessen reproduzierbares Rezept und Release-Patchmanifest**. Ohne ihn kann die Dokumentation den Minimalbuild genau eingrenzen, aber keine kompatible NVRM.kext aus dem Nichts erzeugen.
