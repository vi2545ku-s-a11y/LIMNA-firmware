# LIMNA — mjukvarukedjan mot simulerad sensor

Bygg och testa hela kedjan från sensor till dashboard **innan hårdvaran kommer.**
När sensorerna anländer byter du en rad kod och kopplar in dem.

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

## Etapp 1 — Vattenmodellen

Ingen installation behövs för detta steg.

```bash
python3 sensor_profiles.py --selftest
python3 fake_sensors.py --selftest
```

Det första skriver ut registerkartan: vilka givare profilen innehåller, deras
register och skalfaktorer, vilka register som *inte* läses, strömkanaler,
effekt, fabriksadresser och kabelvarningar. Sist kontrollerar det att
simulatorn och pollaren ser **samma** karta, och stannar med felmeddelande om
de inte gör det.

Det andra skriver ut **nio avsnitt**: ett dygns kurvor, en kontroll av att pH
följer syret, ett regnförlopp, uppvärmningen för syre och pH sida vid sida,
varifrån uppvärmningstiderna kommer, de signerade registren, nivågivarens
skala, sensorernas fabriksadresser och till sist samma kartkontroll.

> Antalet stod tidigare som åtta här och som sex i `etapper.md`. Nio gäller
> från 2026-08-10, när kartkontrollen tillkom.

**KONTROLL:** syret toppar på eftermiddagen kring 11 mg/L och bottnar före
gryningen kring 7,5. Vid regn stiger turbiditeten från 4 till över 30 NTU
samtidigt som konduktiviteten sjunker, nivån stiger och pH sjunker.

Att de rör sig tillsammans är avsiktligt. Det är så en verklig avrinning ser
ut, och det är vad larmreglerna ska klara av att skilja från ett sensorfel.

Tre saker till som testet ska säga:

- **`23 av 23 timsteg ror sig at samma hall`** — pH och syre drivs båda av
  fotosyntesen och ska följas åt. Gör de inte det är modellen fel.
- **−2,51 °C ska läsas som −2,51**, inte som 652,85. Tre register är signerade
  int16 och läses nu som sådana.
- **Konduktivitet och pH står båda på adress 1** från fabrik. Se etapp 8.

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
python3 fake_sensors.py
```
```bash
# fönster 2
python3 poller.py
```

Pollaren skapar `config.json` första gången och börjar mäta var 60:e sekund.
Du ser den slå på railen, vänta ut uppvärmningen, läsa fem sensorer och skicka.
Först skriver den ut vilken sensorprofil den kör, vilka larmgränser den har,
och vad en delad strömrail skulle spara.

> **Sjätte givaren.** Under fälttest sitter en andra syregivare på adress 6
> (krav U-04). Lägg `"optional_sensors": ["do2"]` i `config.json` så tas den
> med, och nyttolasten får fälten `do2` och `do_d` — skillnaden mellan de två
> givarna, som är själva mätstorheten när påväxt ska mätas. Utan raden är
> bojen femgivarig precis som förut.

Gå till Grafana → Explore, välj InfluxDB och kör:

```flux
from(bucket: "limna")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "limna")
  |> filter(fn: (r) => r._field == "do")
```

**KONTROLL:** en punkt dyker upp per mätning, med rätt tidsstämpel.

Detta är det ögonblick då hela kedjan bevisligen fungerar. Allt därefter är
påbyggnad.

> **Otålig?** Sätt `warmup_s` till `5` i `config.json` medan du bygger.
> Kom ihåg att sätta tillbaka det till 90 innan du drar några slutsatser
> om energiförbrukning.

---

## Etapp 4 — Sensorfel

Öppna `faults.json` och ändra en rad medan allt är igång:

```json
"sensor_2_turb": "offline"
```

**KONTROLL:** pollaren loggar `SVARAR INTE efter 3 forsok` för turbiditeten,
**och de övriga tre fortsätter helt normalt.**

Det är krav EL-03 demonstrerat. Ett sensorfel får aldrig ta ner hela mätningen.
Detta är också demoscenario 1 i frontend-specen.

Prova även `"noisy"` — sensorn svarar men värdet hoppar. Pollaren läser varje
givare **tre gånger** per mätning och tar medianen, så ett enstaka skräpvärde
försvinner utan att något behöver ställas in. Spretar alla tre flaggas raden
`"fel":{"ec":"brusig"}`. Det var den enda av de fyra feltyperna som
ingenting letade efter förut.

Prova även `"stuck"` — sensorn svarar men med ett fastfruset värde. Det är det
verkligt lömska felet, eftersom systemet ser friskt ut.

**Pollaren letar numera efter det.** Har ett värde varit bitidentiskt i
`stuck_cycles` mätningar i rad (24 stycken, alltså sex timmar vid riktigt
intervall) flaggas raden med `"fel":{"ph":"stuck"}`. Sänk `stuck_cycles` till
3 i `config.json` om du vill se det hända medan du tittar.

Det är en **misstanke**, inte en dom. Konduktiviteten mäts i hela µS/cm och
kan stå still på samma heltal en stund utan att vara trasig — därför har den
en egen, längre tröskel i profilen (96 mätningar mot 24 för övriga).

### Vad som händer med ett omöjligt värde

Ett värde utanför databladets mätområde — pH 85 därför att skalfaktorn är fel,
eller −2,51 °C läst som 652,85 — **sparas med flaggan `out_of_range`**. Det
kasseras inte, och det ersätts aldrig med ett tidigare värde (krav EL-03).

Ett omöjligt tal är bevis. Kastar man det ser grafen frisk ut med ett hål som
liknar ett kommunikationsfel, och nästa person letar på fel ställe. Bara pH
har ett angivet mätområde i databladen, så bara pH kan kontrolleras — de
övriga fyra säger det rakt ut i självtestet.

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

**Sex regler**, som `larmregler.py` skapar åt dig. Grafana → Alerting →
Alert rules.

| Larm | Regel | Krav |
|---|---|---|
| Lågt syre | `do` under 5,0 i 15 min | F-10 |
| Snabb turbiditetsökning | `turb` ökar mer än 200 % på 3 h | F-11 |
| Mätningar saknas | glapp över tre mätintervall **mellan två mätningar** | F-13 |
| Bojen har inte hört av sig | inget alls levererat på 13 h | F-13 |
| Lågt batteri | `batt` under 12,4 | F-13 |
| Temperaturgivarna oense | `temp_d` över 1,0 °C i 30 min | F-13 |

> Antalet stod tidigare som fyra här och i `larmregler.py`, och som fem i
> `etapper.md`. Sex gäller, och har gjort det sedan regeln "Sensor svarar
> inte" delades i två.

Tidsgränserna räknas ur `config.json` — glappet är tre mätintervall och
tystnaden två ekointervall plus en timme. Kör du `larmregler.py` medan
konfigfilen står på utvecklingsvärden bygger den regler som larmar var tredje
minut, och säger till om det innan den gör det.

**Bojen har egna gränser sedan 2026-08-10.** `alarm_limits` i `config.json`
gör att bojen själv känner igen ett larm och skickar mätningen direkt i
stället för att vänta på nästa sändfönster (krav F-12). Reglerna här ligger
kvar som andra försvarslinje: bojen avgör *när* något skickas, Grafana avgör
vem som väcks, håller larmhistoriken och fångar takt och tystnad som bojen
inte kan se om sig själv. Håll talen i takt — `do` 5,0, `batt` 12,4 och
`temp_d` 1,0 står på båda ställena.

> **De flesta gränserna är PLACEHOLDER.** Krav Ö-08 säger att larmnivåer per
> parameter fastställs av Swedamm tillsammans med kommunen, för den faktiska
> pilotplatsen. Bara `do`, `batt` och `temp_d` är beslutade. Övriga är
> platshållare så att mekaniken går att prova, märkta med `*` i pollarens
> startutskrift och i `_placeholder` i konfigfilen. De ska ersättas före
> pilot.
>
> `ec` och `lvl` har medvetet **inga** gränser. Konduktivitetens driftområde
> är 50–300 µS/cm, så den gräns på 1000 som stod här först hade aldrig löst
> ut — och en gräns som aldrig löser ut är sämre än ingen alls, eftersom den
> ser ut som ett skydd. Nivåns gränser beror på var bojen förankras.

De två första F-13-larmen var tidigare ett enda: "ingen data på 45 minuter".
Det gick inte att behålla när sändningen blev glesare än mätningen — mellan
två sändningar har en fullt frisk boj inte levererat något, och den regeln
kunde inte se skillnad. Den hade larmat varje timme.

Skillnaden nu: en punkts tidsstämpel är den **bojen** satte, så ett glapp i
serien betyder att bojen inte mätte — inte att den inte hann rapportera. Det
är två olika fel med två olika servicebesök.

Testa dem genom att sätta `"oxygen_decline": true` respektive
`"rain_event": true` i `faults.json`.

**KONTROLL:** larmen utlöses, och de återgår när du sätter tillbaka värdena.

Förändringstakt-larmet är det svåraste och det mest värdefulla. Ett fast
gränsvärde säger att något redan hänt; ett takt-larm säger att något håller på
att hända. Det är skillnaden mellan en logger och en produkt.

---

## Etapp 6b — Energisparläge och batteri

`batt` var hårdkodat till 13,1 fram till 2026-08-10, vilket gjorde att
larmet för lågt batteri aldrig kunde provoceras och att ekoläget inte hade
något att reagera på. Nu läses batteriet som vilken parameter som helst
(krav E-07) — på bojen är det en ADC-avläsning, här en konfigurerad källa.

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

Ekoläget låg först på 12,4 V, alltså exakt samma spänning som larmet. Då
började bojen spara ström i samma ögonblick som situationen blev akut, och
läget fick aldrig göra det det finns till för. Urladdningskurvan på LiFePO4 är
dessutom platt ner till omkring 12,8 V och faller sedan brant — vid 12,4 V
återstår runt tio procent. Beräkningsunderlaget 5.2 får sina cirka 30 dygns
uthållighet just genom att anta växling vid halva batteriet, och med den gamla
tröskeln fanns de dygnen inte.

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

**1. ~~Rätta registerkartan.~~ Klart, och den bor numera på ett ställe.**
Adresser och skalfaktorer är verifierade mot databladen 2026-07-29 och ligger
i `sensor_profiles.py`. Både simulatorn och pollaren läser den därifrån —
ingen av dem har en egen kopia, och självtestet stannar om de skulle sluta
säga samma sak.

Kartan gäller **en givarfamilj**, och den är ännu inte upphandlad. Blir det en
annan (till exempel B&C Electronics 8325) lägger du till en profil till och
pekar `config.json` på den med `"sensor_profile"`. Bara driftadresserna 1–5
följer med — register, skalor, fabriksadresser, uppvärmningstider och
ledarfärger är produktspecifika. Ändra inget utan ett nytt datablad i handen.

**Sätt adresserna innan du kopplar in allt.** De fem sensorerna levereras med
fabriksadresserna 55, 50, 1, 26 och 1 — konduktivitet och pH har **samma
adress från fabrik**. Kopplar du in båda innan du ändrat den ena sänder de
samtidigt, svaret blir oläsligt, och styrkortet rapporterar "svarar inte".
Det skickar dig på jakt efter en trasig sensor när felet är en adresskrock.

**Adressregistret är inte samma för alla fem, och ingen har provat det.**
Databladen anger `0x2000` för syre och turbiditet, `0x0200` för konduktivitet
och pH, och `0x0000` för nivån. README och koden påstod till 2026-08-10 att
alla fem använder `0x0200` — det var fel. Registren står nu per givare i
`sensor_profiles.py`, märkta **OVERIFIERAT mot hårdvara**: kontrollera mot
databladets sida innan monteringsdagen, inte under den.

**Återställningsknappen finns bara dokumenterad på konduktivitetsgivaren**
(SW1-SET, håll i tre sekunder så återgår kommunikationsinställningarna till
fabriksläge). README påstod tidigare att alla fem har en. Databladen säger
inget om de övriga fyra — utgå från att du inte kan trycka dig ur ett misstag
på dem.

**Verktyget som gör flytten** är `set_address.py`. Det läser fabriksadress,
driftadress och adressregister ur profilen, så inga registernummer skrivs in
på ett andra ställe.

```bash
python3 set_address.py --list          vad som ska flyttas, och vart
python3 set_address.py --scan          vilka adresser som svarar just nu
python3 set_address.py --sensor ec     flytta konduktivitetsgivaren
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
python3 fake_sensors.py --factory      i ett fönster
python3 set_address.py --scan          i ett annat
```

De simulerade givarna **byter adress på riktigt** — skriver du till
adressregistret försvinner givaren från sin gamla adress och dyker upp på den
nya, precis som på bänken. En repetition som går igenom är alltså bevis för
att *sekvensen* stämmer. Den är inte bevis för att registernumren gör det.
De kommer ur databladen och är märkta OVERIFIERAT tills någon gjort det här
mot en riktig givare.

Adresskrocken syns också i repetitionen: konduktivitet och pH svarar båda på
1 från fabrik, och i fabriksläge svarar den adressen därför inte alls — precis
som två riktiga givare som sänder samtidigt.

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
i koden och är **avstängd**, eftersom svaret är okänt för just dessa givare —
ledtråden i `sensor_databla.md` gäller en annan Seeed-sensor.

Vad det står och faller med, uppmätt i simulering 2026-08-10 med
`warmup_s = 45`:

| | Cykeltid | Syre |
|---|---|---|
| Fast uppvärmning | 45,3 s | 10,43 |
| Adaptiv, hypotesen stämmer | 40,0 s | 10,65 |
| Adaptiv, hypotesen är fel | **0,5 s** | **5,73** |

Sista raden är hela risken. Den ser ut som en enorm energivinst och ger ett
syrevärde 45 procent för lågt, samt ett pH på 5,09 som hade utlöst larm. Slå
inte på flaggan mot hårdvara förrän någon sett vad registret faktiskt
innehåller.

Av de fem tiderna står **två** i databladen: konduktivitet och pH, båda under
2 sekunder. Nivåns 1 sekund är härledd ur en angiven svarstid på 5 ms — hur
snabbt givaren svarar när den väl är igång, inte hur lång tid det tar dit.
Kvar att mäta är syret (antaget 40 s) och turbiditeten (antaget 8 s).

Självtestet skriver ut varifrån varje tid kommer, så gissningarna syns för den
som kör koden och inte bara för den som läser den.

**Det obekväma:** de två sensorer som drar mest ström är de som blir klara
snabbast. Att hålla alla fem påslagna i 90 sekunder för att syresensorn behöver
det kostar ungefär 5 Wh per dygn av en budget på 13,4 — skillnaden mellan att
klara autonomikravet och att missa det. Lösningen är två strömkanaler i stället
för en, så den snabba gruppen kan läsas och stängas av medan syresensorn
fortfarande sätter sig (krav E-11 och E-12).

**Mjukvaran är byggd, hårdvaran är det inte.** Sätt `"split_rail": true` i
`config.json` så slår pollaren på snabba kanalen, väntar ut dess uppvärmning,
läser konduktivitet, pH och nivå, stänger av, och gör sedan om det för syre
och turbiditet. Rail-tillhörighet och uppvärmningstid står per givare i
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
| `sensor_profiles.py` | **Registerkartan, en enda källa.** En profil per givarfamilj |
| `.env` | Lösenord och token. Gitignorerad, skapas ur `.env.example` |
| `fake_sensors.py` | De simulerade Modbus-sensorerna med realistisk vattenkemi |
| `poller.py` | Mätlogiken. Blir så småningom firmware |
| `set_address.py` | Flyttar en givare från fabriksadress till driftadress |
| `sensor_databla.md` | Vad databladen faktiskt säger, och vad de inte säger |
| `etapper.md` | Etappprotokoll: vad som är verifierat och vad som inte är det |
| `larmregler.py` | Skapar de sex larmreglerna i Grafana. Körs en gång |
| `requirements.txt` | Pinnade versioner av pymodbus, paho-mqtt och pyserial |
| `Makefile` | Genvägar. Skriv `make` för att se dem |
| `config.json` | Intervall, uppvärmningstid, adresser. Skapas automatiskt |
| `faults.json` | Felinjektion. Redigera under drift |
| `buffer.db` | Lokal buffert, SQLite. Motsvarar SD-kortet |
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

För utveckling på en laptop är allt detta acceptabelt. För en kommunal pilot
är det inte det.

### K-02 — en känd och medveten skuld

**Detta är inte ett förbiseende.** Att utvecklingsuppsättningen går öppen är
ett beslut, taget för att allt kör över localhost och för att varje krypterat
lager är ytterligare en sak som kan gå sönder medan bufferten och
strömrailen fortfarande ändras. Ett felaktigt certifikat, en utgången
giltighetstid och ett saknat lösenord ger alla samma rad i loggen som ett
täckningsbortfall — `ingen kontakt med brokern` — och den tvetydigheten är
dyr just nu.

Skulden ska betalas **innan piloten i Vombsjön**, inte innan nästa etapp.

Pollaren har sin del förberedd sedan 2026-08-10, avstängd. Att slå på den är
en konfigändring, inte en omskrivning:

```json
"mqtt": {
  "host": "din-server", "port": 8883,
  "topic": "limna/HV-001/measurements",
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
