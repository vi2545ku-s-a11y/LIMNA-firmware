# LIMNA — kontext för Claude Code

## Vad projektet är

LIMNA är en sensorlösning för bönder. En solcellsdriven mätboj flyter i
gårdens bevattningsdamm och mäter vattenkvaliteten, och markfuktsgivare ute
på fälten tillkommer senare. Allt laddas upp till en gemensam databas och
kopplas till väderdata, så att bonden får underlag för var och när det ska
bevattnas.

Bojen går **säsongsvis**, ungefär april–oktober, och tas upp innan isen
lägger sig. Den läser RS485/Modbus-givare, buffrar lokalt och skickar till en
dashboard. I prototypen går sändningen över WiFi; radiolänk (LoRa) eller
mobilnät väljs senare.

Utvecklas av Vilmer och Sean.

Koden i `laptop/` är en **laptop-repetition** av firmwaren. Sensorerna
simuleras så att hela kedjan kan byggas innan hårdvaran har levererats.

## Ska du skriva firmware?

Läs **`firmware/README.md`**. Den är specifikationen: hårdvara och pinnar,
givare och buss, mätcykeln, dataformatet, djupsömn och RTC-minne, bufferten,
uppkopplingen, byggordningen och hur det testas utan hårdvara. Firmwaren
skrivs i **MicroPython** och läser `delad/sensor_profiles.py` oförändrad.
`laptop/poller.py` är facit för logiken.

## Begränsningar som väger tyngre än vanlig kodkvalitet

**Pollerns struktur speglar avsiktligt firmware som ska köra på en ESP32.**
Slå inte ihop mätning och sändning. Ersätt inte SQLite-bufferten med något
elegantare. Gör inte om den blockerande loopen till async. Varje sådan
"förbättring" gör porteringen svårare. SQLite blir SD-kort, `time.sleep`
blir djup sömn, loggraden om strömpåslag blir en riktig MOSFET-switch.

**Registerkartan gäller specifika givare, inte projektet.** Profilen heter
`limna-damm-2026`:

| Enhet | Givare | Fält | Status |
|---|---|---|---|
| 1 | Seeed S-pH&ORP-01A, pH, ORP och temperatur | `ph`, `orp` | Registerkarta ur tillverkarens manual, ej provad mot hårdvara |
| 2 | Seeed S-DTS210-01, grumlighet och temperatur | `turb` | Registerkarta ur tillverkarens manual, **fabriksadressen motsägelsefull** (1 eller 50) |

Det är de enda två givarna. Manualerna ligger i `docs/datablad/` och är
sammanfattade i `sensor_databla.md`.

En givare kan ge **flera mätvärden från samma bussadress**: pH-givaren ger
både pH och ORP. I profilen är det en rad per givare med en lista `values`,
och varje värde blir ett eget fält i nyttolasten.

Alla värden läses som **16-bitars heltal med skalfaktor**: pH ÷ 100, ORP ÷ 10
(signerat, kan bli negativt), grumlighet ÷ 10. pH-givaren har också
flyttalsregister, men deras ordföljd är från fabrik CDAB (lågt ord först), så
de läses inte. Pollarens självtest kontrollerar avkodningen mot manualernas
egna exempelsvar.

Kartan bor på **ett** ställe, som en utbytbar profil. Den stod en gång på två
— `REGISTERS` i `fake_sensors.py` och `SENSORS` i `poller.py` — utan någon
kontroll av att de sade samma sak. Sedan 2026-08-09 är båda vyer av samma
tabell, och `verify_consistency()` stannar om de skulle glida isär. Byte av
givare är att ändra profilen, inte koden.

Filen ligger i `delad/sensor_profiles.py`, medvetet utanför `laptop/`,
eftersom ESP32-firmwaren ska läsa exakt samma fil. **Lägg aldrig en kopia i
`laptop/` eller `firmware/`** — det är precis det felet profilen finns för
att förhindra.

Vad som överlever ett givarbyte och vad som inte gör det:

- **Portabelt:** driftadresserna 1–2. De sätter vi själva vid montering.
- **Produktspecifikt:** fabriksadresser, register, skalfaktorer, signerad
  eller osignerad, uppvärmningstider med källa, matningsspänning och
  ledarfärger.

Ändra inget i kartan utan ett datablad i handen.

**Bussen går på 9600 baud, och båda givarna levereras på 9600.** Ingen baud
behöver ändras. Men båda kan ha **fabriksadress 1**, så grumlighetsgivaren
flyttas till 2 (register 0x2000) innan pH-givaren kopplas in. pH-givaren står
redan rätt på 1. Byter man ändå adress på pH-givaren (register 0x0200) gäller
den nya först efter en strömcykel.

**Grumlighetsgivaren kräver minst 12 V.** Batteriet får sjunka till 10,8 V,
så sensorrailen ska ha en egen omvandlare till 12 V, inte matas direkt från
batteriet.

**Uppvärmningstiderna är inte uppmätta.** pH/ORP-givarens 30 s är
databladets svarstid för ORP (`harledd`). Grumlighetsgivarens 10 s är en
`gissning` — dess datablad säger ingenting. Båda skrivs ut av självtestet,
och ändras tiderna måste källan följa med. Strömmen är databladens övre
gränser (under 10 mA respektive 0,3 W vid 12 V), inte uppmätt.

**Koden ska gå att läsa av någon utan djup programmeringsvana.** Projektägaren
är konstruktör, inte utvecklare. Föredra tydlighet framför korthet. Undvik
ramverk, abstraktionslager och beroenden som inte behövs.

**Språk:** kommentarer i koden på engelska, all användarvänd text och
dokumentation på svenska. README.md är svensk och ska förbli det.

## Det tal som styr allt

Uppvärmningstiden (`warmup_s`, nu 30 s) är den största posten i bojens
energiförbrukning, eftersom sensorrailen hålls påslagen hela tiden. Den styr
solpanelens och batteriets storlek.

Energibudgeten ska räknas på uppmätta värden: uppvärmningstid,
givarnas ström, styrkortets sömnström och radions. Bojen går bara under
säsongen, så den mörkaste månaden att dimensionera för är oktober, inte
november.

Därför är mätningen av den verkliga uppvärmningstiden fortfarande projektets
**enskilt viktigaste mätning**, och uppvärmningssimuleringen i
`fake_sensors.py` är inte en detalj utan själva poängen: den ska visa att en
sensor som läses för tidigt efter strömpåslag ger fel värde. Om något i den
koden ändras måste beteendet kvarstå och verifieras.

## Kravbeteckningar som förekommer i README och kommentarer

De kommer från kravspecifikationen, som inte ligger i denna mapp.

| ID | Krav |
|---|---|
| F-02 | Automatisk växling till energisparläge vid låg batterinivå |
| F-10 | Larm på fasta gränsvärden |
| F-11 | Larm på snabb förändring, alltså förändringstakt |
| F-12 | Larm sänds omedelbart, oavsett ordinarie sändintervall |
| F-13 | Larm vid låg batterinivå, utebliven kommunikation, sensor som tystnar |
| E-07 | Batterinivå rapporteras som ordinarie mätparameter |
| E-11 | Sensorrailen SKA byggas delbar |
| E-12 | Sensorrailen BÖR delas i två strömkanaler |
| EL-03 | En sensor som inte svarar får inte stoppa övriga mätningar |
| EL-05 | Automatisk återstart efter programfel |
| EL-07 | Omvandlare väljs efter verklig last, inte industristorlek |
| EL-08 | Modemet har egen spänningsomvandlare |
| EL-09 | Omvandlare för avvikande spänning placeras efter railbrytaren |
| K-02 | All överföring till server ska vara krypterad |
| K-03 | Lokal lagring när uppkopplingen saknas, automatisk eftersändning |
| K-04 | Lokal lagring ska räcka för minst 30 dygns mätdata |
| Ö-08 | Larmnivåer per parameter fastställs för den faktiska platsen — för dammbojen tillsammans med bonden |
| D-01 till D-09 | Krav på dashboarden: karta, grafer, status, larmhistorik, export |

## Korsvis kontroll som designmönster

En givare som slutar svara är lätt att upptäcka. En givare som fortsätter
svara i tid, med ett rimligt men felaktigt tal, är det inte. En sådan sensor
slutar inte svara — den börjar ljuga.

Koden löser det genom att ge värdet något att jämföras med:

| Kontroll | Jämför | Fångar | Dammbojen |
|---|---|---|---|
| fastfruset värde | ett värde mot sin egen historik | en givare som frusit fast | aktiv |
| `temp_d` | flera termometrar i samma vatten | en givare vars kort driver | aktiv, två termometrar |

Båda givarna har en termometer. `temp` kommer från grumlighetsgivarens
Pt1000 (±0,3 °C), och `temp_d` är skillnaden mot pH-givarens (±0,5 °C). Över
0,8 °C (`TEMP_D_TOLERANCE_C`) är de mer oense än databladen tillåter.

Påväxt på grumlighetsgivarens mätfönster är det största underhållsproblemet
i en varm, näringsrik damm. Den syns som en grumlighet som sakta stiger medan
givaren svarar normalt — simulatorns `drift` på `sensor_2_turb`. pH-givarens
motsvarighet är en åldrande referenselektrod som driver.

## Sådant som ser ut som buggar men inte är det

- Mätvärden skrivs till disk **innan** något försök görs att skicka dem. Det
  är avsiktligt och är krav K-03. Motsatt ordning tappar data varje gång
  täckningen viker, vilket ute vid en damm sker ofta.
- Tidsstämpeln sätts i bojen, inte på servern. En mätning som legat i
  bufferten i tre dygn ska hamna på rätt tidpunkt i grafen.
- Pollaren sover mellan mätningarna i stället för att schemalägga. Det speglar
  djup sömn på mikrokontrollern.

## Aldrig

- Committa `buffer.db`, `config.json` eller `faults.json`.
- Lägga till autentisering eller TLS i utvecklingsuppsättningen utan att
  fråga. Att den är öppen är ett medvetet val för laptopbruk, dokumenterat
  sist i README.
- Ändra nyttolastens fältnamn (`ph`, `orp`, `turb`, `temp`, `temp_d`,
  `batt`) utan att fråga. De är korta för att spara data och används av
  telegraf, dashboarden och larmreglerna. De ersatte `do` och `lvl`
  2026-11-23, när syre- och nivågivaren byttes mot Seeed-givarna.

  Nyttolasten är **187 byte** med dammbojens fem mätvärden, uppmätt i
  pollarens självtest.
