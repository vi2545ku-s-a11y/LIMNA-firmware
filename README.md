# LIMNA — mjukvarukedjan mot simulerad sensor

Bygg och testa hela kedjan från sensor till dashboard **innan hårdvaran kommer.**
När sensorerna anländer byter du en rad kod och kopplar in dem.

**Bojen ligger i en bevattningsdamm** och går under bevattningssäsongen,
ungefär april–oktober. Den har två givare från Seeed: en pH-givare som mäter
pH, redoxpotential (ORP) och temperatur, och en grumlighetsgivare som mäter
grumlighet och temperatur. Manualerna ligger i `docs/datablad/` och är
sammanfattade i `sensor_databla.md`.

```
                 sensor_profiles.py
              (registerkartan, en enda källa)
                    │           │
                    ▼           ▼
fake_sensors.py  ──Modbus──▶  poller.py  ──MQTT──▶  mosquitto
                                  │                     │
                              buffer.db             telegraf
                            (SQLite, lokal)             │
                                                    influxdb
                                                        │
                                                    grafana  ◀── du tittar här
```


---

## Innan du börjar

Två saker att installera. Inget annat.

| | |
|---|---|
| **Docker Desktop** | Kör de fyra serverdelarna. Ladda ner från docker.com |
| **Python 3.10+** | Kör de två skripten. Troligen redan installerat |

```bash
pip install -r requirements.txt
```

Versionerna är pinnade med avsikt. pymodbus API ändras kraftigt mellan
utgåvor, och koden är skriven mot 3.8. Uppgradera inte utan att köra
självtesterna efteråt.

Allt annat sköter Docker.

---

## Så ligger repot

```
delad/       registerkartan. Läses av laptopkoden OCH av firmwaren
laptop/      laptoprepetitionen: simulator, pollare, verktyg
firmware/    ESP32-koden. Ännu inte påbörjad, se firmware/README.md
docs/datablad/  tillverkarens manualer för de två givarna (PDF)
```

Dataplattformen — `docker-compose.yml` och konfigfilerna — ligger kvar i
roten, eftersom Docker Compose läser `.env` ur sin egen mapp.

**`sensor_profiles.py` ligger i `delad/` och inte i `laptop/`**, för att
firmwaren ska läsa exakt samma registerkarta. Den kopieras till ESP32:ns
filsystem vid flashning. Laptopprogrammen når den med tre rader överst i
filen som lägger `delad/` på importvägen — en kopia per mapp hade varit
precis det felet profilen finns för att förhindra.

`config.json`, `buffer.db` och `faults.json` skapas i `laptop/`, av
programmen som äger dem.

> **Python-kommandona nedan körs från `laptop/`.** Det är inte kosmetiskt:
> kartkontrollen jämför simulatorn med pollaren och hittar dem där de ligger.
> Kör du `make` i stället sköts det åt dig.

---

## Etapp 1 — Vattenmodellen

Ingen installation behövs för detta steg.

```bash
cd laptop
python3 ../delad/sensor_profiles.py --selftest
python3 fake_sensors.py --selftest
```

Eller, från roten: `make selftest`.

Det första skriver ut registerkartan: vilka givare profilen innehåller, deras
register och skalfaktorer, vilka register som *inte* läses, strömkanaler,
effekt, fabriksadresser och kabelvarningar. Sist kontrollerar det att
simulatorn och pollaren ser **samma** karta, och stannar med felmeddelande om
de inte gör det.

Det andra skriver ut **sju avsnitt**: ett dygns kurvor, ett regnförlopp,
uppvärmningen för pH och grumlighet sida vid sida, varifrån
uppvärmningstiderna kommer, signerad avkodning av ORP, sensorernas
fabriksadresser och till sist samma kartkontroll.

**KONTROLL:** pH toppar på eftermiddagen kring 8,0 och bottnar före gryningen
kring 7,3. ORP följer samma dygn mellan ungefär 235 och 285 mV. Grumligheten
ligger kring 6 NTU, stiger till ungefär 10 när det bevattnas 05–08, och till
närmare 50 vid regn.

Tre saker till som testet ska säga:

- **ORP är signerat.** Läses registret som osignerat blir −12,5 mV till
  6541,1 mV. Se kolumnen "osignerat".
- **En för tidig avläsning ger fel värde som ser rimligt ut.** pH läst efter
  några sekunder visar runt 4,5 i stället för 7,9.
- **Båda givarna står på adress 1** från fabrik. Se etapp 8.

`python3 poller.py --selftest` kontrollerar dessutom att profilen avkodar
manualernas egna exempelsvar till de värden manualerna anger.

---

## Etapp 2 — Starta dataplattformen

```bash
cp .env.example .env      # forsta gangen: hemligheterna hamnar dar
docker compose up -d
docker compose ps
```

Fyra containrar ska vara igång. Öppna sedan Grafana på
**http://localhost:3000** och logga in med `admin` / `limna`.

Datakällan behöver du inte lägga till för hand. Den provisioneras vid start
från `grafana/provisioning/datasources/influxdb.yml`, tillsammans med en
färdig dashboard från `grafana/dashboards/limna.json`.

**KONTROLL:** under Connections → Data sources finns **InfluxDB**, och
Save & test svarar "datasource is working". Under Dashboards finns mappen
**LIMNA** med översikten. Den är tom tills etapp 3 skickat något.

> Token står numera bara i `.env`. Den behövde tidigare ändras på tre
> ställen — `docker-compose.yml`, `telegraf.conf` och provisioning-filen.
> Se avsnittet **Hemligheter** längre ned, särskilt varför ett byte kräver
> `docker compose down -v`.

---

## Etapp 3 — Första mätvärdet hela vägen

Två terminalfönster.

```bash
# fönster 1
cd laptop && python3 fake_sensors.py
```
```bash
# fönster 2
cd laptop && python3 poller.py
```

Eller `make sim` respektive `make poll` från roten.

Pollaren skapar `config.json` första gången och börjar mäta var 60:e sekund.
Du ser den slå på railen, vänta ut uppvärmningen, läsa givarna och skicka.
Först skriver den ut vilken sensorprofil den kör, vilka larmgränser den har,
och vad en delad strömrail skulle spara.

Gå till Grafana → Explore, välj InfluxDB och kör:

```flux
from(bucket: "limna")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "limna")
  |> filter(fn: (r) => r._field == "ph")
```

**KONTROLL:** en punkt dyker upp per mätning, med rätt tidsstämpel.

Detta är det ögonblick då hela kedjan bevisligen fungerar. Allt därefter är
påbyggnad.

> **Otålig?** Sätt `warmup_s` till `5` i `config.json` medan du bygger.
> Kom ihåg att sätta tillbaka det till 30 innan du drar några slutsatser
> om energiförbrukning. Med för kort uppvärmning läser pH för lågt och
> spretar, med flaggan `brusig` — det är simuleringen som gör sitt jobb.

---

## Etapp 4 — Sensorfel

Öppna `faults.json` och ändra en rad medan allt är igång:

```json
"sensor_2_turb": "offline"
```

**KONTROLL:** pollaren loggar `SVARAR INTE efter 3 forsok` för grumligheten,
**och pH-givaren fortsätter helt normalt.** Temperaturen faller tillbaka på
pH-givarens termometer, och `temp_d` försvinner ur nyttolasten — en
termometer kan inte vara oense med sig själv.

Det är krav EL-03 demonstrerat. Ett sensorfel får aldrig ta ner hela mätningen.
Detta är också demoscenario 1 i frontend-specen.

Prova även `"noisy"` — sensorn svarar men värdet hoppar. Pollaren läser varje
givare **tre gånger** per mätning och tar medianen, så ett enstaka skräpvärde
försvinner utan att något behöver ställas in. Spretar alla tre flaggas raden
`"fel":{"ph":"brusig"}`. Det var den enda av de fyra feltyperna som
ingenting letade efter förut.

Prova även `"stuck"` — sensorn svarar men med ett fastfruset värde. Det är det
verkligt lömska felet, eftersom systemet ser friskt ut.

**Pollaren letar numera efter det.** Har ett värde varit bitidentiskt i
`stuck_cycles` mätningar i rad (24 stycken, alltså sex timmar vid riktigt
intervall) flaggas raden med `"fel":{"turb":"stuck"}`. Sänk `stuck_cycles` till
3 i `config.json` om du vill se det hända medan du tittar.

Det är en **misstanke**, inte en dom. Tröskeln står per givare i profilen,
eftersom ett grovt kvantiserat värde ärligt kan stå still länge.

### Vad som händer med ett omöjligt värde

Ett värde utanför databladets mätområde — ORP 6541 mV därför att ett
signerat register lästes som osignerat — **sparas med flaggan
`out_of_range`**. Det
kasseras inte, och det ersätts aldrig med ett tidigare värde (krav EL-03).

Ett omöjligt tal är bevis. Kastar man det ser grafen frisk ut med ett hål som
liknar ett kommunikationsfel, och nästa person letar på fel ställe. Alla tre
värdena har ett angivet mätområde — pH 0–14, ORP ±2000 mV, grumlighet
0–1000 NTU — och självtestet skriver ut dem.

---

## Etapp 5 — Buffring vid täckningsbortfall

```bash
docker compose stop mosquitto
```

Låt pollaren gå i några minuter. Den loggar att brokern inte svarar och att
mätningarna ligger kvar. Starta sedan:

```bash
docker compose start mosquitto
```

**KONTROLL:** vid nästa mätning skickas allt eftersläpande i efterhand, och i
Grafana fylls hålet i med **rätt ursprungliga tidsstämplar** — inte klumpade
vid återanslutningen.

Det sista är själva poängen och kravet K-03. Telegraf är konfigurerad att läsa
tiden ur meddelandet, inte ur klockan när det togs emot.

---

## Etapp 6 — Larm

**Fem regler**, som `larmregler.py` skapar åt dig. Grafana → Alerting →
Alert rules.

| Larm | Regel | Krav |
|---|---|---|
| pH utanför gränserna | `ph` utanför 6,0–9,0 i 15 min (PLACEHOLDER) | F-10 |
| Grumligt vatten | `turb` över 50 NTU i 30 min (PLACEHOLDER) | F-10 |
| Mätningar saknas | glapp över tre mätintervall **mellan två mätningar** | F-13 |
| Bojen har inte hört av sig | inget alls levererat på 13 h | F-13 |
| Lågt batteri | `batt` under 12,4 | F-13 |

> Ett taktlarm (F-11) läggs till här när bonden sagt vad "snabbt" är för
> just den dammen — grumlighet som stiger fort är regn eller en pump som rör
> upp botten, pH som stiger fort är en algblomning på väg.
>
> Den gamla regeln "Lagt syre" tas bort av `larmregler.py` om den finns kvar
> i Grafana.

Tidsgränserna räknas ur `config.json` — glappet är tre mätintervall och
tystnaden två ekointervall plus en timme. Kör du `larmregler.py` medan
konfigfilen står på utvecklingsvärden bygger den regler som larmar var tredje
minut, och säger till om det innan den gör det.

**Bojen har egna gränser.** `alarm_limits` i `config.json`
gör att bojen själv känner igen ett larm och skickar mätningen direkt i
stället för att vänta på nästa sändfönster (krav F-12). Reglerna här ligger
kvar som andra försvarslinje: bojen avgör *när* något skickas, Grafana avgör
vem som väcks, håller larmhistoriken och fångar takt och tystnad som bojen
inte kan se om sig själv. Håll talen i takt — `ph` 6,0–9,0, `turb` 50 och
`batt` 12,4 står på båda ställena.

> **Alla gränser utom batteriets är PLACEHOLDER.** Vilket pH och vilken
> grumlighet som är ett problem bestäms tillsammans med bonden, för den
> faktiska dammen. Platshållarna är märkta med `*` i pollarens startutskrift
> och i `_placeholder` i konfigfilen.
>
> `orp` har medvetet **ingen** gräns ännu. Lågt eller negativt ORP betyder
> syrefritt vatten, men var gränsen går beror på dammen.

De två första F-13-larmen var tidigare ett enda: "ingen data på 45 minuter".
Det gick inte att behålla när sändningen blev glesare än mätningen — mellan
två sändningar har en fullt frisk boj inte levererat något, och den regeln
kunde inte se skillnad. Den hade larmat varje timme.

Skillnaden nu: en punkts tidsstämpel är den **bojen** satte, så ett glapp i
serien betyder att bojen inte mätte — inte att den inte hann rapportera. Det
är två olika fel med två olika servicebesök.

Testa pH- och grumlighetslarmen genom att sätta `"bloom": true` i
`faults.json` (simulerad algblomning: pH och grumlighet stiger timme för
timme), eller `"rain_event": true` för en grumlighetstopp.

**KONTROLL:** larmet utlöses, och det återgår när du sätter tillbaka värdet.

---

## Etapp 6b — Energisparläge och batteri

Batteriet läses som vilken parameter som helst (krav E-07) — på bojen är det
en ADC-avläsning, här en konfigurerad källa så att ekoläget och larmet för
lågt batteri går att provocera.

För att se krav F-02 hända, sätt i `config.json`:

```json
"battery": {"source": "sim", "volt": 13.1, "drain_v_per_h": 60.0}
```

**KONTROLL:** spänningen sjunker, och vid **13,0 V** loggar pollaren
`[F-02] gar i ENERGISPARLAGE` och byter till 60 minuters mätintervall och
sex timmars sändintervall. Bojen går tillbaka till normalt läge först över
13,2 V — två gränser i stället för en, så den inte fladdrar på gränsvärdet.
Fortsätter du ner till 12,4 V utlöser dessutom larmet för lågt batteri och
mätningen skickas direkt (F-12).

### Varför 13,0 V och inte 12,4

De tre spänningarna gör tre olika saker och får inte sammanfalla:

| Spänning | Vad som händer |
|---|---|
| **13,2 V** | tillbaka till normalt läge |
| **13,0 V** | in i energisparläge — ungefär halva kapaciteten på 4S LiFePO4 |
| **12,4 V** | larm om lågt batteri, servicebesök |

Ligger ekoläget på samma spänning som larmet börjar bojen spara ström i samma
ögonblick som situationen blir akut, och läget får aldrig göra det det finns
till för. Urladdningskurvan på LiFePO4 är platt ner till omkring 12,8 V och
faller sedan brant — vid 12,4 V återstår runt tio procent. Därför växlar
bojen redan vid halva batteriet.

`drain_v_per_h: 60` är orimligt snabbt med flit, så du slipper vänta. Sätt
tillbaka `"source": "fixed"` efteråt.

---

## Etapp 7 — Dashboarden

Bygg panelerna som kravspecens kapitel 9 kräver: karta med bojens position,
grafer med valbar tidsperiod, batteri- och kommunikationsstatus, larmhistorik
och CSV-export.

Spara dashboarden som JSON och lägg den i versionshantering. Då är den
återskapbar och inte något som bara finns i en container.

---

## Etapp 8 — När hårdvaran kommer

Tre ändringar, i denna ordning:

**1. Registerkartan bor på ett ställe: profilen `limna-damm-2026`** i
`sensor_profiles.py`. Både simulatorn och pollaren läser den därifrån —
ingen av dem har en egen kopia, och självtestet stannar om de skulle sluta
säga samma sak.

Båda givarnas register kommer ur **tillverkarens manualer**
(`docs/datablad/`, sammanfattade i `sensor_databla.md`). Ingen av dem är ännu
provad mot riktig hårdvara.

**Ingen baudrate behöver ändras.** Båda givarna levereras på 9600 baud, samma
som bussen.

**Sätt adresserna innan du kopplar in allt.** Båda givarna levereras på
**adress 1**. Kopplar du in båda innan du ändrat den ena sänder de samtidigt,
svaret blir oläsligt, och styrkortet rapporterar "svarar inte". Det skickar
dig på jakt efter en trasig sensor när felet är en adresskrock. pH-givaren
får behålla 1; grumlighetsgivaren flyttas ensam till 2.

**Grumlighetsgivarens fabriksadress är osäker.** Manualens registertabell
säger 50, dess kommandoexempel använder 1. `--scan` frågar båda.

**Adressregistret skiljer sig mellan givarna.** pH-givaren använder `0x0200`
och tar den nya adressen först efter en strömcykel. Grumlighetsgivaren
använder `0x2000`.

**Mata givarna med reglerade 12 V.** Grumlighetsgivaren kräver 12–24 V och
klarar inte ett batteri som sjunkit under 12 V.

**Verktyget som gör flytten** är `set_address.py`. Det läser fabriksadress,
driftadress och adressregister ur profilen, så inga registernummer skrivs in
på ett andra ställe.

```bash
cd laptop
python3 set_address.py --list          vad som ska flyttas, och vart
python3 set_address.py --scan          vilka adresser som svarar just nu
python3 set_address.py --sensor turb   flytta grumlighetsgivaren till adress 2
```

Verktyget **vägrar skriva medan mer än en givare svarar**, och det vägrar om
givaren inte svarar på adressen den skulle stå på. En skrivning på en delad
adress går till båda givarna, så den kontrollen är inte en artighet. Efter
skrivningen kontrolleras att den nya adressen svarar *och* att den gamla har
tystnat — det är först då flytten har hänt.

`--dry-run` visar exakt vad som skulle skrivas utan att skriva. `--yes`
hoppar över frågan.

Öva hela sekvensen utan hårdvara:

```bash
cd laptop && python3 fake_sensors.py --factory      i ett fönster
cd laptop && python3 set_address.py --scan          i ett annat
```

De simulerade givarna **byter adress på riktigt** — skriver du till
adressregistret försvinner givaren från sin gamla adress och dyker upp på den
nya, precis som på bänken. En repetition som går igenom är alltså bevis för
att *sekvensen* stämmer. Den är inte bevis för att registernumren gör det.
De kommer ur manualerna och är inte provade förrän någon gjort det här mot
en riktig givare.

Adresskrocken syns också i repetitionen: båda givarna svarar på 1 från
fabrik, och i fabriksläge svarar den adressen därför inte alls — precis som
två riktiga givare som sänder samtidigt. "Koppla ur" pH-givaren genom att
sätta `"sensor_1_ph": "offline"` i `faults.json`; då svarar
grumlighetsgivaren ensam på 1 och kan flyttas. Sätt tillbaka `"ok"` efteråt
och `--scan` visar båda, på 1 och 2.

**2. Byt transport.** Numera en rad i `config.json`, inte en ändring i koden:

```json
"modbus": {
  "transport": "rtu",
  "serial_port": "/dev/ttyUSB0",
  "timeout_s": 1.0
}
```

`"tcp"` är simulatorn, `"rtu"` är riktiga givare över RS485. Inget annat
ändras — registerkartan, skalorna, omförsöken, bufferten och larmlogiken är
desamma på båda.

**Baudrate, paritet, databitar och stoppbitar står inte i `config.json`.** De
är egenskaper hos givarfamiljen och hämtas ur profilens `serie`-post, samma
ställe som registren kommer ifrån — byter du familj följer de med. Vilken
`/dev/tty…` omvandlaren dyker upp på är däremot en egenskap hos *maskinen*,
och den står därför i konfigfilen.

Pollaren skriver ut vilken buss den använder på startraden `Buss`, så det går
att se på en rad om den talar med simulatorn eller med hårdvara.

RTU kräver `pyserial`, som ligger pinnat i `requirements.txt`. Den behövs inte
för laptopkörningen.

> **Att öppna serieporten är inte att slå på strömmen.** På laptopen *är*
> anslutningen strömpåslaget, och det är vad som gör simulatorns uppvärmning
> ärlig. Över RTU stämmer det inte längre: raderna `[ström på]` och
> `[ström av]` i `measure_rail()` är där MOSFET:en ska växlas på riktigt, och
> tills den kroken finns läser `"rtu"` givare som någon annan redan gett
> ström. Mätvärdet blir rätt. Energibesparingen blir inte av.

**3. Mät uppvärmningstiden.** Sänk `warmup_s` stegvis och se när värdena börjar
avvika. Skriv in det uppmätta värdet i energibudgeten. Det talet styr den
största enskilda posten i bojens energiförbrukning.

**Titta samtidigt på vad registret innehåller under de första sekunderna.**
Returnerar givaren `0` innan den är klar, eller ett värde som bara är fel?
Frågan avgör om `"adaptive_warmup": true` går att använda: då läser pollaren
tills värdet blir giltigt i stället för att vänta en fast tid. Flaggan finns
i koden och är **avstängd**, eftersom svaret är okänt för just dessa givare.

Risken med den, i simulering: med fel hypotes kortas cykeln från 30 till
0,5 s och pH-värdet blir runt 45 procent för lågt.
Det ser ut som en enorm energivinst och ger skräpdata. Slå inte på flaggan
mot hårdvara förrän någon sett vad registret faktiskt innehåller.

Ingen av tiderna är en uppmätt uppstartstid. pH-givarens 30 s är
databladets **svarstid** för ORP — hur snabbt givaren följer en ändring när
den väl är igång, inte hur lång tid det tar dit. Grumlighetsgivarens 10 s är
en ren gissning: dess datablad säger ingenting.

Självtestet skriver ut varifrån varje tid kommer, så gissningarna syns för den
som kör koden och inte bara för den som läser den.

**Strömkanaler.** Grumlighetsgivaren antas klar på några sekunder,
pH-givaren behöver en halv minut. Med två strömkanaler kan grumligheten läsas
och stängas av medan pH-elektroden fortfarande sätter sig (krav E-11 och
E-12). Grumlighetsgivaren är dessutom den som drar mest, 0,3 W mot högst
0,12 W, så det är den som lönar sig att stänga av tidigt.

**Mjukvaran är byggd, hårdvaran är det inte.** Sätt `"split_rail": true` i
`config.json` så slår pollaren på snabba kanalen, väntar ut dess uppvärmning,
läser grumligheten, stänger av, och gör sedan om det för pH-givaren. Rail-tillhörighet och uppvärmningstid står per givare i
profilen. Den andra MOSFET:en finns dock inte i bojen ännu, så med en
strömkanal mäter flaggan bara samma givare i en krångligare ordning.

Pollaren skriver ut vad delningen skulle spara **även när flaggan är av**,
räknat ur profilens uppvärmningstider och effekter — så beslutet kan fattas
på en siffra utan att du först kör ett dygn i delat läge.

Därefter portas logiken till ESP32. Strukturen är avsiktligt densamma:
SQLite blir SD-kort, `time.sleep` blir djup sömn, och railen slås av på riktigt
med MOSFET-switchen i stället för att bara loggas.

---

## Filer

| Fil | Vad den gör |
|---|---|
| `delad/sensor_profiles.py` | **Registerkartan, en enda källa.** En profil per givarfamilj. Läses även av firmwaren |
| `firmware/README.md` | Vad ESP32-koden ska bli, och i vilken ordning |
| `.env` | Lösenord och token. Gitignorerad, skapas ur `.env.example` |
| `laptop/fake_sensors.py` | De simulerade Modbus-sensorerna med realistisk vattenkemi |
| `laptop/poller.py` | Mätlogiken. Blir så småningom firmware |
| `laptop/set_address.py` | Flyttar en givare från fabriksadress till driftadress |
| `sensor_databla.md` | Vad databladen faktiskt säger, och vad de inte säger |
| `docs/datablad/` | Seeeds manualer för pH/ORP-givaren och grumlighetsgivaren. Källan till registerkartan |
| `etapper.md` | Etappprotokoll: vad som är verifierat och vad som inte är det |
| `laptop/larmregler.py` | Skapar de fyra larmreglerna i Grafana. Körs en gång |
| `laptop/requirements.txt` | Pinnade versioner av pymodbus, paho-mqtt och pyserial |
| `Makefile` | Genvägar. Skriv `make` för att se dem |
| `laptop/config.json` | Intervall, uppvärmningstid, adresser. Skapas automatiskt |
| `laptop/faults.json` | Felinjektion. Redigera under drift |
| `laptop/buffer.db` | Lokal buffert, SQLite. Motsvarar SD-kortet |
| `docker-compose.yml` | De fyra serverdelarna |
| `mosquitto.conf` | Brokerns konfiguration |
| `telegraf.conf` | Bryggan MQTT → InfluxDB |
| `grafana/` | Datakälla och dashboard som provisioneras vid start |

---

## Hemligheter

Lösenord och token ligger i `.env`, som `.gitignore` utesluter. Första gången:

```bash
cp .env.example .env
docker compose up -d
```

`.env.example` ligger i versionshantering med platshållare i stället för
värden, så nästa person ser vilka variabler som behövs utan att någon
hemlighet följer med.

InfluxDB-token stod tidigare på **tre ställen** — `docker-compose.yml`,
`telegraf.conf` och Grafanas provisioning-fil — och måste hållas i takt för
hand. Nu står den på ett: Compose läser `.env` och skickar värdet vidare till
telegraf och Grafana, som expanderar det i sina egna konfigurationsfiler.

> **Byter du `INFLUX_TOKEN` räcker det inte att starta om.** InfluxDB sätter
> upp sig själv en enda gång, vid första starten, och sparar token i sin
> volym. Efter det ger en ny token bara 401 mot en databas som fortfarande
> har den gamla. För att byta på riktigt krävs `docker compose down -v`,
> vilket raderar all mätdata.

---

## Detta är inte produktionsklart

Medvetna genvägar som måste åtgärdas innan skarp drift:

- MQTT går okrypterat och utan lösenord. Krav K-02 kräver kryptering.
- Ingen övervakning av att plattformen själv lever.
- Ingen säkerhetskopiering av databasen.

För utveckling på en laptop är allt detta acceptabelt. För en pilot på en
gård är det inte det.

### K-02 — en känd och medveten skuld

**Detta är inte ett förbiseende.** Att utvecklingsuppsättningen går öppen är
ett beslut, taget för att allt kör över localhost och för att varje krypterat
lager är ytterligare en sak som kan gå sönder medan bufferten och
strömrailen fortfarande ändras. Ett felaktigt certifikat, en utgången
giltighetstid och ett saknat lösenord ger alla samma rad i loggen som ett
täckningsbortfall — `ingen kontakt med brokern` — och den tvetydigheten är
dyr just nu.

Skulden ska betalas **innan första piloten på en gård**, inte innan nästa etapp.

Pollaren har sin del förberedd, avstängd. Att slå på den är
en konfigändring, inte en omskrivning:

```json
"mqtt": {
  "host": "din-server", "port": 8883,
  "topic": "limna/LIMNA-001/measurements",
  "tls": {"enabled": true, "ca_cert": "/etc/limna/ca.crt"},
  "username_env": "LIMNA_MQTT_USER",
  "password_env": "LIMNA_MQTT_PASS"
}
```

Lösenordet står inte i konfigfilen — där står bara *namnet* på miljövariabeln.

Vad som krävs, i den ordning det ska göras. **Inget av det är gjort, och
TLS-koden i pollaren har aldrig anslutit till en broker med certifikat:**

| # | Del | Vad som krävs |
|---|---|---|
| 1 | Certifikat | Let's Encrypt om brokern får ett publikt namn, annars en egen CA vars rotcert läggs i bojen. Servercertifikatet måste ha **alla namn klienterna använder** i SAN-fältet — pollaren ansluter till serverns namn utifrån, telegraf till `mosquitto` inifrån Docker-nätverket. Saknas det ena får den klienten `certificate verify failed` |
| 2 | mosquitto | TLS-lyssnare på **8883**, `allow_anonymous false`, lösenordsfil skapad med `mosquitto_passwd` (kör den i containern — den lokala installationen är avinstallerad) |
| 3 | telegraf | Samma TLS och samma inloggning mot brokern |
| 4 | Bojen | Rotcertet i filsystemet, och en rutin för att förnya det **innan** det går ut. En boj med utgånget certifikat slutar leverera och ser i loggen ut som ett täckningsproblem |
| 5 | Grafana | Bakom HTTPS, med eget lösenord ur `.env` i stället för `admin` / `limna` |
| 6 | InfluxDB | Ny token i `.env` och `docker compose down -v`, så utvecklingstoken inte följer med till drift |

`insecure: true` finns i konfigurationen och stänger av kontrollen av
värdnamn. Använd den bara för ett första test mot en IP-adress. Den tar bort
det mesta av poängen med TLS, och den ska inte finnas kvar i en pilot.
