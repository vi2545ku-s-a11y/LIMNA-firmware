# LIMNA — etappprotokoll

Alla etapper explicit, med kommandon, förväntat resultat och kontrollpunkt.
Bocka av i högerkolumnen allteftersom.

**Detta dokument skiljer på två saker:**

| Markering | Betyder |
|---|---|
| ✅ **Verifierad** | Har körts och fungerade |
| 🚧 **Påbörjad** | Delvis körd, resten pågår |
| ⬜ **Inte körd** | Har inte gjorts ännu |
| 🔒 **Blockerad** | Går inte att köra förrän något annat är löst |

Att en etapp är verifierad betyder att logiken håller.

> **Not 2026-07-29:** etapp 0 löstes genom att macOS uppgraderades. Etapp 2–5
> kördes därefter **på din maskin**, inte på en annan. Den tidigare
> reservationen "på sin maskin, inte din" gäller alltså inte längre för dem.

---

## Vad som ändrades 2026-07-29 — fem sensorer

Bojen har inte längre fyra sensorer utan **fem**. En pH-givare har tillkommit
och nivågivaren har flyttat från adress 4 till 5, eftersom den numera sitter
på sjöbotten i stället för i sensorkorgen. Samma RS485-buss, bara ny adress.

| Adress | Sensor | Modell | |
|---|---|---|---|
| 1 | Löst syre | S-RJY-01 | oförändrad |
| 2 | Turbiditet | S-DTS210-01 | oförändrad |
| 3 | Konduktivitet + temp | S-EC-01 | oförändrad |
| 4 | **pH** | S-pH-01A | **ny** |
| 5 | Nivå | S-YW-01B | flyttad från 4 |

**Registerkartan är inte längre påhittad.** Adresser och skalfaktorer är
verifierade mot databladen. Tre register är signerade int16 — konduktivitetens
och pH:ns temperatur samt nivåvärdet — och läses nu som sådana. Tidigare
lästes allt osignerat, vilket hade gjort −2,51 °C till 652,85 °C.

**Uppvärmningstiderna är däremot inte alla verifierade.** Tre står i
databladen, två är fortfarande gissningar:

| Sensor | Tid | Källa |
|---|---|---|
| Löst syre | 40 s | **gissning**, optisk fluorescens |
| Turbiditet | 8 s | **gissning**, optisk |
| Konduktivitet | 2 s | datablad |
| pH | 2 s | datablad |
| Nivå | 1 s | **härledd** ur en angiven svarstid på 5 ms |

Nivåns källa stod tidigare som "datablad" i tabellen ovan, medan brödtexten
längre ned, `CLAUDE.md` och `WARMUP_SOURCE` sa "härledd". **Härledd gäller.**
Databladet anger responstid — hur snabbt givaren svarar när den väl är igång
— vilket inte är samma sak som uppstartstid. Rättat 2026-08-10.

Nivåsensorns strömförbrukning är inte publicerad någonstans och saknas därför
i energibudgeten.

**Nyttolasten är nu 199 byte**, inte de 120 energibudgeten antar. Gapet fanns
redan med fyra sensorer (176 byte). pH la till 10, temperaturkontrollen
`temp_d` 13.

Siffran var först angiven till 193. Det var för lågt: exempelnyttolasten i
självtestet använde bekvämt korta mätvärden och skrev `ec` som heltal, men
`decode_register()` avrundar och returnerar alltid en float, så det står
`"ec":318.0` i varje verkligt meddelande. Uppmätt spann över 300 verkliga
nyttolaster är 197 till 201 byte, medelvärde 199,1. Rättat 2026-08-02.

---

## Vad som ändrades 2026-08-10

Fem ändringar i koden och tio rättade motsägelser i dokumenten.

### Koden

**Registerkartan bor på ett ställe.** Ny fil `sensor_profiles.py` med en
deklarativ profil per givarfamilj: driftadress, fabriksadress, adressregister,
register, skalfaktor, signerat, mätområde, uppvärmningstid **med källa**,
strömkanal, effekt, vilka register som inte läses, och kabelvarningar.
`REGISTERS` i simulatorn och `SENSORS` i pollaren är nu vyer av den. Ett
självtest jämför de två vyerna och **stannar** om de skiljer sig — kontrollen
som saknades helt tidigare.

**Delad strömrail (E-11, E-12).** `"split_rail": true` kör snabb kanal och
långsam kanal var för sig. Avstängning i `finally`. Cykeltid per kanal loggas.
Besparingen räknas ut och loggas **även när flaggan är av**.

**Sjätte givaren (U-04).** En andra syregivare på adress 6, valfri via
`"optional_sensors": ["do2"]`. Nyttolasten får `do2` och `do_d`. Påväxt
simuleras med `drift` på bara den ena, och rör då **inte** termometern — så
påväxt och kortdrift går att skilja åt.

**Larmgränser i bojen (F-12).** `alarm_limits` i `config.json`, per parameter
med undre och övre gräns. `bradskande()` utvärderar mot dem och en larmande
mätning skickas direkt — efter att den skrivits till bufferten, aldrig före.
Grafana-reglerna ligger kvar som andra försvarslinje.

**Fastfruset värde.** Ett värde som varit bitidentiskt i `stuck_cycles`
mätningar i rad flaggas i `fel`-fältet. Default 24, alltså sex timmar vid
riktigt intervall.

**Batteri och energisparläge (E-07, F-02).** `batt` är inte längre hårdkodat
till 13,1. Källan konfigureras: `fixed` som förut, eller `sim` som tömmer ett
batteri i vald takt så att larmet och ekoläget går att provocera. Under
**13,0 V** går bojen i energisparläge — 60 minuters mätintervall och sex
timmars sändintervall — och tillbaka först över **13,2 V**.

> **Rättat samma dag: trösklarna var först 12,4 / 12,8 V och det var fel.**
> 12,4 V är larmgränsen i F-13, så bojen började spara ström i exakt samma
> ögonblick som situationen blev akut och ekoläget fick aldrig göra det det
> finns till för. På 4S LiFePO4 är urladdningskurvan platt ner till omkring
> 12,8 V och faller sedan brant; vid 12,4 V återstår cirka tio procent.
> Beräkningsunderlaget 5.2 räknar sina ~30 dygn på växling vid **halva**
> batteriet, vilket i vilospänning är omkring 13,0 V. Med de gamla
> trösklarna fanns de 30 dygnen inte.
>
> Nu gör de tre spänningarna tre olika saker: 13,2 V tillbaka till normalt,
> 13,0 V in i ekoläge, 12,4 V larm och servicebesök.

**Adaptiv uppvärmning (OVERIFIERAT, avstängd).** `"adaptive_warmup": true`
låter pollaren läsa tills givarna svarar i stället för att vänta en fast tid.
Simulatorn kan spela hypotesen med `"cold_returns_zero": true` i
`faults.json`. Se mätningen längre ned — den säger varför flaggan är av.

**Databehandling per mätning — beslutad, inte uppkommen.** Ospecificerad
tidigare. Nu: **tre avläsningar, median**. Medianen behöver inget
utliggarkriterium — ett skräpvärde flyttar ett medelvärde med en tredjedel av
sitt fel och en median inte alls, och varje tröskelbaserat filter kräver ett
tal som någon måste försvara.

Kostnaden är två extra avläsningar på en rail som redan är påslagen, alltså
cirka 20 ms bussid mot 90 sekunders uppvärmning. I energibudgeten syns det
inte.

Tre följder:

| Fall | Vad som händer |
|---|---|
| Ett skräpvärde av tre | Medianen tar det, ingen flagga |
| Tre värden som spretar | Raden flaggas `brusig` |
| Värde utanför databladets mätområde | **Sparas**, flaggas `out_of_range` |

Ett omöjligt värde kasseras aldrig och ersätts aldrig med ett tidigare (krav
EL-03). Det är bevis — för trasig givare, fel skalfaktor eller ett signerat
register läst som osignerat. Kastar man det ser grafen frisk ut med ett hål
som liknar ett kommunikationsfel.

**`noisy` fångas nu.** Det var den enda av de fyra feltyperna i `faults.json`
som ingenting i koden letade efter. Uppmätt 2026-08-10 med `warmup_s = 45`:
friska givare ger inga flaggor alls, och `"sensor_3_ec": "noisy"` ger
`konduktivitet 289.0 <- 3 avlasningar spred sig 58 (20 procent)` och
`fel: {'ec': 'brusig'}`.

> **Två villkor, inte ett.** Spridningen måste överstiga både andelen
> (`spread_warn_frac`, 0,10) **och** två steg av registrets egen upplösning.
> Med bara andelen larmar turbiditeten i klart vatten: vid 0,4 NTU och 0,1
> NTU upplösning är ett enda sista-siffran-steg 25 procent, och ingenting är
> fel. Upplösningen kommer ur profilens skalfaktor.
>
> **Vid kort `warmup_s` flaggas friska givare som brusiga.** Det är samma
> fälla som med larmgränserna: värdena är genuint ostabila då. Trimma inte
> `spread_warn_frac` med kort uppvärmning.

Endast pH har ett angivet mätområde i profilen, så bara pH kan
områdeskontrolleras. De övriga fyra säger det rakt ut i självtestet i stället
för att låta någon anta att de kontrolleras.

**K-02 förberett, inte påslaget — och hemligheterna flyttade.** TLS och
inloggning finns i pollarens sändkod, avstängda. Lösenord läses ur
miljövariabler vars *namn* står i konfigfilen. InfluxDB-token, Grafanas
lösenord och InfluxDB:s lösenord ligger nu i `.env` i stället för på tre
ställen i versionshanterade filer. Beslutet att låta laptopuppsättningen gå
öppen togs medvetet 2026-08-10 — se README, avsnittet "K-02 — en känd och
medveten skuld", som listar vad som krävs och i vilken ordning inför piloten.

### Dokumenten

| # | Motsägelse | Vad som gäller |
|---|---|---|
| 1 | Antal larmregler: docstring och README sa fyra, detta dokument fem | **Sex**. Rättat på alla tre ställena |
| 2 | Avsnitt i självtestet: README sa åtta, detta dokument sex | **Nio** sedan kartkontrollen tillkom |
| 3 | Adressregister: README, detta dokument och koden sa `0x0200` för alla fem | Tre olika register enligt `sensor_databla.md`, märkta **OVERIFIERAT** |
| 4 | Återställningsknapp: README sa alla fem | Endast **konduktivitetsgivaren** är dokumenterad |
| 5 | `sensor_databla.md` kap 2 hette "de krockar inte" men visade att de krockar | Rubriken heter nu "två av fem krockar" |
| 6 | Länktabellen listade fyra datablad | pH:s rad tillagd, med noteringen att länken saknas |
| 7 | `requirements.txt` sa macOS 12.7.5 | **macOS 26.6**, uppgraderad 2026-07-28 |
| 8 | `send_interval_s` 120 i config mot 3600/21600 i `larmregler.py` | `config.json` är enda källan. `eco_send_interval_s` tillagd, ekoläget är fortfarande **inte implementerat** |
| 9 | Nyttolastens storlek | 199 byte gäller. Inget dokument påstod 120 som verklig storlek — de tre träffarna gäller alla energibudgetens **felaktiga antagande**, vilket är korrekt beskrivet |
| 10 | Nivåns uppvärmningskälla: tabellen sa "datablad" | **Härledd** ur en svarstid på 5 ms |

Punkt 8 har en följd att känna till: reglerna byggs nu ur `config.json`, som
normalt står på utvecklingsvärden. `larmregler.py` varnar innan den skapar
något. **Regeln "Matningar saknas" som redan ligger i Grafana har kvar sin
gamla gräns på 2700 s** — skriptet hoppar över regler som finns. Vill du ha
den härledda gränsen får du radera regeln och köra om skriptet.

---

## Nuläge

| Etapp | Vad den bevisar | Status |
|---|---|---|
| 0 | Docker eller motsvarande går att köra | ✅ Löst — Docker Desktop 4.78.0 |
| 1 | Vattenmodellen beter sig som en sjö | ✅ Verifierad, nu med pH |
| 2 | Dataplattformen startar | ✅ Verifierad |
| 3 | Hela kedjan hänger ihop | ✅ Verifierad **hela vägen till Grafana**, fem sensorer |
| 4 | Ett sensorfel tar inte ner resten | ✅ Verifierad — alla fyra feltyperna |
| 5 | Inget tappas vid täckningsbortfall | ✅ Verifierad **mot databasen** |
| 6 | Larmen fungerar | 🚧 Påbörjad — 2 av 6 verifierade skarpt i Grafana. Bojens egna gränser (F-12) verifierade i självtest och i skarp körning |
| 7 | Dashboarden visar det den ska | ⬜ Inte körd (inte längre blockerad) |
| 8 | Riktiga sensorer | 🚧 Registerkartan klar och flyttad till en profil, delad rail byggd i mjukvara, hårdvaran saknas |

---

## ETAPP 0 — Docker ✅ LÖST

macOS uppgraderades 2026-07-28 från 12.7.5 till **26.6**, vilket tog bort hela
problemet. Docker Desktop **4.78.0** installerades och fungerar utan den
nedpinnade versionen och utan den workaround som tidigare krävdes.

Inget av nedanstående behövdes. Det sparas bara som historik.

<details>
<summary>De fyra vägarna som övervägdes innan uppgraderingen</summary>

Docker Desktop krävde macOS 13. Maskinen körde 12.7.5. Halva systemet —
InfluxDB och Grafana — hade därför aldrig startat.

### Väg A — Colima

En Docker-ersättare byggd just för att slippa Docker Desktop.

```bash
brew install colima docker docker-compose
colima start
docker ps
```

Fungerar det går allt annat i detta dokument oförändrat.

**Osäkerhet:** jag vet inte säkert att Colima stöder macOS 12. Prova först,
det tar tio minuter. Fungerar det inte, gå till väg B.

### Väg B — Installera de fyra delarna direkt

```bash
brew install influxdb telegraf grafana
brew services start influxdb
brew services start grafana
```

Mosquitto har du redan installerat.

Nackdel: InfluxDB måste konfigureras för hand via `http://localhost:8086`, och
token måste sedan matchas ihop på tre ställen — `telegraf.conf`,
`grafana/provisioning/datasources/influxdb.yml` och där du startar telegraf.
Räkna med en halvtimme.

Nackdel två: `docker-compose.yml` används då inte, och du testar inte samma
uppsättning som ni sedan ska köra i skarp drift.

### Väg C — En VPS

Dit ska ni ändå. De 80 kronorna i månaden står redan i komponentlistan.

Mer arbete nu, men då kör ni den riktiga uppsättningen, och senare kan bojen
skicka data över 4G på riktigt i stället för över localhost.

### Väg D — Uppgradera macOS

Behövs förr eller senare oavsett. **Det blev denna.**

</details>

| **KONTROLL** | ✅ `docker ps` svarar utan fel. Docker 29.5.3, daemon igång. |
|---|---|

---

## ETAPP 1 — Vattenmodellen ✅

Ingen installation behövs. Detta är det snabbaste sättet att se att
resonemanget håller.

```bash
python3 sensor_profiles.py --selftest
python3 fake_sensors.py --selftest
```

Det första skriver ut registerkartan ur profilen och kontrollerar till sist
att simulatorn och pollaren ser samma karta. Det andra skriver ut **nio**
avsnitt.

> Antalet stod tidigare som sex här och som åtta i README, medan koden skrev
> åtta. **Nio gäller** från 2026-08-10, då kartkontrollen tillkom som ett
> nionde avsnitt. Rättat på båda ställena.

**1. Ett dygns kurvor.** Syret ska toppa på eftermiddagen kring 11 mg/L och
bottna före gryningen kring 7,5. Det är växternas fotosyntes på dagen och
förbrukning på natten.

**2. pH mot syre.** Testet räknar själv och ska skriva `23 av 23 timsteg ror
sig at samma hall`. Att pH följer syret är ingen tillfällighet: båda drivs av
fotosyntesen. Växterna förbrukar koldioxid i dagsljus, vilket höjer pH, och
producerar syre samtidigt. **Rör de sig åt olika håll är modellen fel** — eller,
när hårdvaran är på plats, elektroden.

**3. Ett regnförlopp.** Turbiditeten ska stiga från cirka 4 till över 30 NTU
inom en halvtimme, samtidigt som konduktiviteten sjunker från 312 till runt
270, nivån stiger och pH sjunker en halv enhet. Att de rör sig **tillsammans**
är hela poängen — det är så avrinning ser ut, och det är vad larmreglerna ska
kunna skilja från ett sensorfel.

**4. Uppvärmningen, syre mot pH.** Syresensorn ska läsa alldeles för lågt vid
noll sekunder och konvergera mot sant värde kring 40. pH ska vara klart efter
två. Kolumnerna står bredvid varandra just för att skillnaden ska synas.

**5. Varifrån uppvärmningstiderna kommer.** Ska visa att två av fem står i
datablad, att nivåns är härledd och att syrets 40 s och turbiditetens 8 s är
gissningar. Detta avsnitt saknades i uppräkningen tidigare.

**6. Signerade register.** −2,51 °C ska stå som −2,51 i kolumnen `signerat`
och som 652,85 i `osignerat`. Den sista kolumnen är vad den gamla koden hade
gjort.

**7. Nivågivarens skala.** Ska visa vad samma registervärde blir vid 0, 1, 2
och 3 decimaler, och vad det kostar att anta 1000 rakt av.

**8. Fabriksadresser.** Ska visa att konduktivitet och pH båda kommer på
adress 1.

**9. Kartkontroll.** Nytt 2026-08-10. Ska visa `OK` för värderegister,
temperaturregister, uppvärmningstid, strömkanal och temperaturkälla — alltså
att simulatorn och pollaren bygger sin karta ur samma profil. Går det inte
ihop stannar testet med felmeddelande i stället för att skriva en varning.

| **KONTROLL** | Alla nio ser ut som beskrivet. Särskilt: pH-testet säger OK, syrets fel är stort vid 0 s och nära noll vid 40 s, −2,51 läses som −2,51, och kartkontrollen säger OK på alla fem raderna. |
|---|---|

---

## ETAPP 2 — Starta dataplattformen ✅

```bash
docker compose up -d
docker compose ps
```

Fyra containrar ska vara igång: `limna-mosquitto`, `limna-influxdb`,
`limna-telegraf`, `limna-grafana`.

Öppna `http://localhost:3000`, logga in med `admin` / `limna`.

Datakällan behöver du **inte** lägga till för hand. Den provisioneras vid
start, tillsammans med en färdig dashboard.

| **KONTROLL** | ✅ Datakällan `limna-influx` provisionerad mot `http://influxdb:8086`, org `hydrovault`, bucket `limna`, Flux. Mappen LIMNA finns med dashboarden "LIMNA — översikt". |
|---|---|

> **Inloggningen:** användarnamnet är bokstavligen `admin` — inte ditt eget
> namn. Grafana svarar `user not found` om man skriver något annat, vilket
> lätt förväxlas med fel lösenord.

> **Token flyttad 2026-08-10.** Den stod på tre ställen —
> `docker-compose.yml`, `telegraf.conf` och provisioning-filen — och måste
> hållas i takt för hand. Nu står den i `.env`, som `.gitignore` utesluter,
> och Compose skickar värdet vidare till telegraf och Grafana. Kör
> `cp .env.example .env` första gången.
>
> Ett **byte** av token kräver `docker compose down -v`: InfluxDB sätter upp
> sig en enda gång och sparar token i sin volym. En ny token utan det ger
> bara 401 mot en databas som fortfarande har den gamla.

---

## ETAPP 3 — Första mätvärdet hela vägen ✅

Två terminalfönster.

```bash
# fönster 1
python3 fake_sensors.py
```

```bash
# fönster 2
python3 poller.py
```

Pollaren skriver ut vad den gör: slår på railen, väntar ut uppvärmningen,
läser fem sensorer, sparar och skickar.

En cykel ska se ut så här:

```
  [ström på] sensorrail
  [väntar]   90 s uppvärmning
    loest syre       7.98
    turbiditet       4.1
    konduktivitet    312.0
    pH               8.16
  [skala]    nivagivaren rapporterar 3 decimaler -> delar med 1000
    niva             2.337
    temperatur       18.08
      spridning      0.2
  [ström av] sensorrail
```

**Raden om skalan ska bara komma en gång.** Nivågivarens skala står i register
`0x0003` och läses vid uppstart, inte varje cykel — den kan inte ändras medan
bojen ligger i vattnet, och en extra läsning per cykel kostar busstid och ström.

**Raden om spridning** är de fyra temperaturgivarna jämförda med varandra. Se
etapp 4.

Gå till Grafana → Explore och kör:

```flux
from(bucket: "limna")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "limna")
  |> filter(fn: (r) => r._field == "do")
```

| **KONTROLL** | ✅ 28 rader i `buffer.db`, alla `sent=1`, mot exakt 28 punkter i InfluxDB. Ett mot ett, inget tappat, inga dubbletter. |
|---|---|

> **Otålig?** Sätt `warmup_s` till `5` i `config.json` medan du bygger. Sätt
> tillbaka till 90 innan du drar slutsatser om energi. **Och innan du rör
> larmgränser** — se varningen i etapp 6.

**Hela kedjan är nu verifierad**, inte bara fram till MQTT. Grafana hämtar
data genom sin egen datakälla, så även sista hoppet är bevisat.

**Uppmätt skillnad mellan uppvärmningstiderna**, samma tid på dygnet:

| `warmup_s` | Syrevärde | Kommentar |
|---|---|---|
| 90 | 7,98 | Sant. Sensorn har hunnit sätta sig |
| 5 | ~5,15 i snitt | Cirka **en tredjedel för lågt** |

Den tidigare noterade siffran 10,87 mättes mitt på dagen. Syrekurvan toppar på
eftermiddagen och bottnar före gryningen, så absolutvärdet beror på klockslag —
det är *skillnaden* mellan raderna som är poängen.

---

## ETAPP 4 — Sensorfel ✅

Detta är testet som bevisar krav EL-03.

Öppna `faults.json` medan allt kör och ändra en rad:

```json
"sensor_2_turb": "offline"
```

**Verifierat beteende:** pollaren loggar `SVARAR INTE efter 3 forsok` för
turbiditeten, och de tre andra sensorerna fortsätter helt normalt.

Prova även de två andra feltyperna:

| Fel | Vad som händer | Uppmätt 2026-07-29 |
|---|---|---|
| `offline` | Sensorn svarar inte alls | `SVARAR INTE efter 3 forsok (OSError)`, övriga opåverkade |
| `stuck` | Svarar, men med fastfruset värde | Exakt `4,44` varje cykel — men bara vid `warmup_s = 90`, se rutan nedan |
| `noisy` | Svarar med kraftigt brus | Konduktivitet 306,6–307,8 → **184,7–449,1**. Flaggas `brusig` sedan 2026-08-10 |
| `drift` | Svarar normalt, men värdet vandrar | pH +0,15/timme: 8,15 → 10,03 efter 12 h → mättas vid 14,00 |

Raderna i `faults.json` heter nu `sensor_1_do`, `sensor_2_turb`,
`sensor_3_ec`, **`sensor_4_ph`** och **`sensor_5_lvl`** — nivån har flyttat
från 4 till 5.

`noisy` mättes på konduktiviteten, inte på syret. Syresensorn har 40 sekunders
egen uppvärmning och bär därför eget brus vid kort `warmup_s`, vilket gör den
olämplig att skilja felet från bakgrunden med. Konduktiviteten är satt efter
en sekund och annars stabil på tiondelen — där syns 25-procentsbruset direkt.

### `drift` — felet som inte ser ut som ett fel

`drift` är pH-elektrodens verkliga åldrande, och det är en annan sorts fel än
de tre andra. Referensjunktionen sätter igen och lutningen degraderar, så
avläsningen vandrar bort från sanningen över veckor. **Sensorn slutar aldrig
svara. Den börjar ljuga, långsamt.**

| Timmar | Frisk | Driftande |
|---|---|---|
| 0 | 8,15 | 8,15 |
| 6 | 7,80 | 8,70 |
| 12 | 8,25 | 10,03 |
| 24 | 8,17 | 11,75 |
| 48 | 8,17 | **14,00** (mättad) |

Efter ett dygn ligger den 3,6 pH fel och svarar helt normalt. Registret mättas
vid databladets 1400 — en riktig sensor kan inte rapportera pH 15.

### De fyra termometrarna

Fyra av fem sensorer mäter vattentemperatur. Bara en siffra hamnar i
nyttolasten, från konduktivitetsgivaren. De andra tre läses som **kontroll**.

Fyra instrument i samma vatten ska vara överens inom sitt kalibreringsfel.
Nyttolasten bär därför `temp_d` — största avståndet mellan de fyra:

| Läge | `temp_d` |
|---|---|
| Alla friska | ~0,2 °C (kalibreringsfel plus kvantisering) |
| Syregivarens termometer `stuck` | **7,08 °C** |
| Syregivarens kort `drift`, efter 12 h | 1,20 °C |

Under `stuck`-testet förblev `temp` korrekt på 18,11 — konduktivitetsgivaren
var frisk, så mätvärdet stämde och `temp_d` flaggade ändå felet. Det är hela
avsikten: nio byte för ett fel vi annars inte hade något sätt att se.

`stuck` på huvudvärdet **fångas numera av pollaren**, sedan 2026-08-10. Har
ett värde varit bitidentiskt i `stuck_cycles` mätningar i rad — 24 stycken,
alltså sex timmar vid riktigt intervall — flaggas raden med
`"fel":{"ph":"stuck"}`. Ingen Grafana-regel letar efter det, eftersom
telegraf släpper `fel`-fältet; flaggan syns i loggen och i `buffer.db`. Se
"Kvar att göra" sist i dokumentet.

| **KONTROLL** | ✅ Turbiditeten loggade `SVARAR INTE efter 3 forsok (OSError)`. De tre övriga fortsatte oförändrat. Mätningen skickades ändå. |
|---|---|

> **Testa `stuck` med `warmup_s = 90`, annars luras du.** Det fastfrusna värdet
> körs genom uppvärmningsdistorsionen i `fake_sensors.py:255-257` *efter* att
> det satts. Vid `warmup_s = 5` är turbiditetssensorn bara till 5/8 satt, så
> konstanten 4,44 kommer ut som 3,68 / 3,54 / 3,71 — den ser ut att variera
> normalt. Vid 90 s läser den exakt **4,44** varje cykel och felet syns direkt.
>
> Ironin är att `stuck` kallas det lömska felet just för att systemet ser
> friskt ut — och med kort uppvärmning gäller det även för den som letar
> efter det.

> **Historisk not:** i den första versionen fungerade inte detta test.
> Registret behöll sitt gamla värde och pollaren läste det glatt vidare —
> etapp 4 visade alltså en sensor som verkade fungera perfekt. Felet ligger
> nu i `validate()`, vilket ger ett äkta Modbus-undantag.

---

## ETAPP 5 — Buffring vid täckningsbortfall ✅

Detta bevisar krav K-03.

```bash
docker compose stop mosquitto
```

Låt pollaren gå i några minuter. Den ska logga att brokern inte svarar och att
mätningarna ligger kvar i bufferten.

```bash
docker compose start mosquitto
```

**Verifierat beteende:** sex mätningar skickades i efterhand, med sina
ursprungliga tidsstämplar 90 sekunder isär — inte hopklumpade vid
återanslutningen.

Det sista är hela poängen. `telegraf.conf` innehåller raden:

```
json_time_key = "t"
```

Den säger att tiden ska läsas ur mätningen, inte ur klockan när meddelandet
togs emot. Utan den hamnar tre dygns eftersläpande data på fel plats i grafen.

| **KONTROLL** | ✅ Verifierad mot databasen 2026-07-29. |
|---|---|

**Detta är den kontrollpunkt som aldrig gick att pröva förut**, eftersom det
inte fanns någon graf att fylla i — InfluxDB hade aldrig startat. Den tidigare
bocken gällde bara fram till MQTT.

Mätserien från körningen:

```
brokern stoppad 22:36:07Z
  22:37:02  22:38:02  22:39:02  22:40:02  22:41:02   <- buffrade, 5 st
brokern startad  22:41:57Z
```

Alla fem hamnade i InfluxDB på **sina ursprungliga tidsstämplar**, alltså före
återanslutningen, exakt 60 sekunder isär. Hade `json_time_key` inte fungerat
skulle de klumpats ihop kring 22:42. Serien är obruten tvärs igenom bortfallet.

> Notera att färsk data *inte* skiljer de två fallen åt — då är bojens tid
> ungefär lika med mottagningstiden. Bara eftersändning avgör saken.

---

## ETAPP 6 — Larm 🚧

**De sex reglerna är byggda och ligger i Grafana**, i mappen LIMNA,
regelgruppen `limna-larm`. De skapades via `larmregler.py`, som går att köra om
— den hoppar över regler som redan finns i stället för att klaga.

> **Antalet var fel på tre ställen.** Detta dokument sa fem, README och
> docstringen i `larmregler.py` sa fyra, och filen innehöll sex. **Sex
> gäller**, och har gjort det sedan "Sensor svarar inte" delades i två
> regler. Rättat på alla tre ställena 2026-08-10.

| Larm | Regel | Krav | Status |
|---|---|---|---|
| Lågt syre | `do` under 5,0 i 15 min | F-10 | ✅ Utlöste **och** återgick |
| Snabb turbiditetsökning | `turb` ökar mer än 200 % på 3 h | F-11 | 🚧 Logiken bevisad, 3-timmarsfönstret inte |
| Mätningar saknas | glapp över 45 min mellan två mätningar | F-13 | ✅ Utlöste 2026-08-02 på ett verkligt glapp |
| Bojen har inte hört av sig | inget levererat på 13 h | F-13 | ⬜ Byggd, inte provocerad |
| Lågt batteri | `batt` under 12,4 | F-13 | ⬜ Går inte att provocera |
| Temperaturgivarna oense | `temp_d` över 1,0 i 30 min | F-13 | ⬜ Byggd, verifierad i modellen, aldrig utlöst skarpt |

De två första F-13-raderna var till 2026-08-02 en enda regel, "Sensor svarar
inte", som letade efter frånvaro av data de senaste 45 minuterna. Den gick
inte att behålla när steg 2 gjorde sändningen glesare än mätningen: mellan
två sändningar har en frisk boj inte levererat något, och regeln hade larmat
varje timme. I ekoläget, som sänder var sjätte timme, hade den larmat
oavbrutet.

Uppdelningen bygger på att tidsstämpeln sätts i bojen. Ett glapp i serien
betyder därför att bojen **inte mätte**, oavsett när data kom fram — medan
total tystnad är ett ström-, modem- eller täckningsfel. Två olika fel, två
olika servicebesök.

Det andra larmet kan inte gå snabbare än sändintervallet. Tills nästa
leverans är väntad ser en tyst boj och en död boj likadana ut från servern.
Det är priset för att skicka i klumpar, inte ett fel i regeln.

**Den sista regeln, `temp_d`, är den enda som fångar en sensor som ljuger.**
De fem andra letar efter värden utanför gränser, förändringstakt eller
tystnad. En driftande givare gör inget av det — den svarar i tid med ett
rimligt tal som bara råkar vara fel. Det enda som avslöjar den är att de
andra tre termometrarna säger något annat.

Gränsen 1,0 °C är satt över kalibreringsfelet (~0,2) och under vad en riktig
drift ger inom ett halvdygn (1,2 efter 12 timmar). Den är inte uppmätt mot
hårdvara.

**F-10, uppmätt 2026-07-29.** `oxygen_decline` drog ner syret under 5,0.
Larmet gick `inactive → pending → firing` med utlösning 09:20:20Z, exakt 15
minuter efter att datan började flöda — alltså precis regelns `for`-tid. När
`oxygen_decline` slogs av återhämtade sig syret till 10,02 och larmet gick
tillbaka till `Normal`. Båda riktningarna bevisade.

**F-13 sensor svarar inte, verifierad av en olycka.** Laptopen somnade i natt,
inga mätningar kom in på flera timmar, och regeln löste ut. Att orsaken var en
sovande dator och inte en trasig sensor spelar ingen roll för logiken — den
upptäckte tystnaden. När mätningarna återkom gick den tillbaka till `Normal`.

**~~Lågt batteri kan inte provoceras.~~ Går nu att provocera.** `batt` var
hårdkodat till 13,1, så regeln kunde aldrig lösa ut. Sedan 2026-08-10 finns
en konfigurerbar batterikälla. Uppmätt samma dag, med `"source": "sim"` och
60 V/h tömning:

```
cykel 1: batt=12.45 V  normalt      matintervall=900 s
cykel 2: batt=12.38 V  ENERGISPAR   matintervall=3600 s  larm: batt under 12,4
cykel 3: batt=12.32 V  ENERGISPAR   matintervall=3600 s  larm: batt under 12,4
```

Det är bojens egen gränskontroll (F-12) och ekoläget (F-02) som verifierats
här — **inte Grafana-regeln**, som fortfarande aldrig utlöst skarpt.

**F-11 byggdes med två frågefönster** i stället för ett: ett på de senaste
15 minuterna och ett tre timmar bak, och däremellan `($C - $D) / $D * 100`.
Ett förändringstakt-larm går inte att uttrycka som ett fast gränsvärde.

**F-11:s logik är bevisad, men inte dess tidsfönster.** Eftersom det inte fanns
tre timmars sammanhängande historik byggdes en kopia av regeln med
referensfönstret 20 minuter bak i stället för 3 timmar — identisk i övrigt.
Med `rain_event` påslaget:

```
09:44:29Z  regnet startar, turbiditet 4,3
09:56:20Z  larmet loser ut          turbiditet 22,6   (+425 %)
           rain_event av
           turbiditeten ater 4,31 -> larmet ater inactive
```

Testregeln är raderad. **Kvar att göra: samma sak med det riktiga
3-timmarsfönstret**, vilket kräver att pollaren får gå oavbrutet i tre timmar
först. Det är samma matematik med andra tal, men det är inte prövat.

**Regnets signatur, uppmätt genom hela kedjan:**

| tid | turbiditet | konduktivitet | nivå |
|---|---|---|---|
| 11:45:52 | 4,29 | 304,4 | 2,351 |
| 11:47:23 | 6,93 | 300,6 | 2,364 |
| 11:48:54 | 9,53 | 297,1 | 2,377 |
| 11:50:25 | 12,12 | 294,1 | 2,391 |

Alla tre rör sig samtidigt — partiklar in, konduktiviteten utspädd, nivån upp.
Ett sensorfel hade rört en enda av dem. Det är den skillnaden en takt-regel kan
utnyttja och en fast gräns inte.

> **Modellnot:** stänger du av `rain_event` nollställs `rain_started_at` direkt
> och turbiditeten hoppar tillbaka till 4,3 i nästa cykel. Den sextimmars
> avklingning som finns i `_rain_factor` syns alltså bara om du låter flaggan
> stå kvar på `true` och väntar. Det ser ut som en bugg men är det inte.

> ### ⚠️ Trimma aldrig larmgränser med kort uppvärmning
>
> Vid `warmup_s = 5` läser syresensorn 4,5–5,4 i stället för sitt sanna 8,0.
> Det ligger under gränsen 5,0, så **"Lågt syre" gick i `pending` av sig själv,
> utan att något var fel med vattnet.** När uppvärmningen sattes till 90
> återgick den till `inactive` direkt.
>
> Ett falsklarm som kommer från mätmetoden, inte från sjön. Med 90 sekunders
> uppvärmning i tre fjärdedelar av energibudgeten är detta inte en teoretisk
> risk — det är samma avvägning sedd från larmsidan.

**Räkna med att detta tar tid.** Regnhändelsen byggs upp över cirka 20 minuter
i realtid, och syreminskningen över timmar. Du kan inte snabbspola. Sätt igång
det, gör något annat, kom tillbaka.

Förändringstakt-larmet är det svåraste och det mest värdefulla. Ett fast
gränsvärde säger att något **redan** hänt. Ett takt-larm säger att något
**håller på** att hända. Det är skillnaden mellan en logger och en produkt.

| **KONTROLL** | Varje larm utlöses när du framkallar det, och återgår när du sätter tillbaka värdet. |
|---|---|

> **Känd begränsning:** vilken sensor som fallerat når aldrig dashboarden.
> Telegraf släpper fältet `fel` eftersom det innehåller text i stället för
> siffror. Larmet fungerar ändå via det saknade mätvärdet, men i datan syns
> inte vilken sensor det gällde. Går att lösa med `json_string_fields`, men
> det ändrar fältuppsättningen — ta det som ett eget beslut.

---

## ETAPP 7 — Dashboarden ⬜

**Inte längre blockerad.** En färdig dashboard finns redan i `grafana/dashboards/limna.json`
med sju grafer plus en panel för senaste värden.

**pH-panelen ligger bredvid syret med flit.** De två kurvorna ska följas åt
över dygnet eftersom båda drivs av fotosyntesen. Sida vid sida syns det direkt
när pH slutar följa syret — och det är så en åldrande elektrod ger sig till
känna. Flytta inte isär dem för att få en snyggare layout.

Värdepanelen högst upp visar även `temp_d`, spridningen mellan de fyra
temperaturgivarna. Den är en kontroll, inte en mätstorhet.

Bygg vidare tills kravspecens kapitel 9 är täckt:

| Krav | Panel |
|---|---|
| D-01 | Karta med bojens position |
| D-02 | Grafer med valbar tidsperiod |
| D-03 | Batteri-, kommunikations- och kalibreringsstatus |
| D-04 | Larmhistorik |
| D-05 | CSV-export |

Spara dashboarden som JSON och lägg den i versionshantering. Annars finns den
bara inuti en container och försvinner nästa gång du kör `docker compose down -v`.

| **KONTROLL** | Samtliga krav D-01 till D-05 går att peka på i gränssnittet. |
|---|---|

---

## ETAPP 8 — När hårdvaran kommer 🚧

### 8.1 ~~Rätta registerkartan~~ ✅ KLAR — och flyttad till ett ställe

Gjord 2026-07-29, verifierad mot databladen. Flyttad 2026-08-10 till
`sensor_profiles.py`, som numera är **enda källan**. Både `REGISTERS` i
simulatorn och `SENSORS` i pollaren är vyer av den profilen.

Ändra inget utan ett nytt datablad i handen, och **städa framför allt inte**.
Att kartan ser inkonsekvent ut är sant, inte slarvigt: turbiditeten skalar med
10, syret med 100, konduktiviteten med 1. Tre register är signerade och resten
inte. Varje tillverkare gör som den vill.

> **Kartan stod tidigare på två ställen** utan någon kontroll av att de sa
> samma sak. Det är åtgärdat: `sensor_profiles.py --selftest` jämför
> simulatorns och pollarens vyer och **stannar** om de skiljer sig. Samma
> kontroll körs sist i båda de andra självtesten.

Kartan gäller en **givarfamilj**, och den är inte upphandlad. Byte av familj
ska vara att lägga till en profil och peka `config.json` på den med
`"sensor_profile"`. Bara driftadresserna 1–5 (6 under fälttest) är portabla.

### 8.1b Sätt adresserna INNAN du kopplar in allt

Detta steg fanns inte tidigare och är det som faktiskt kan sänka en
monteringsdag.

De fem sensorerna levereras med varsin fabriksadress:

| Sensor | Fabriksadress | |
|---|---|---|
| Löst syre | 55 | |
| Turbiditet | 50 | |
| Konduktivitet | **1** | ⚠️ |
| pH | **1** | ⚠️ |
| Nivå | 26 | |

**Konduktivitet och pH kommer på samma adress.**

Kopplar du in båda innan du ändrat den ena sänder de samtidigt på samma
trådar. Vågformerna överlappar och ramen faller på checksumman. Mastern får
inte två svar — den får ingenting den kan tolka, och rapporterar **"svarar
inte"**.

Det är det missvisande i det hela. Felmeddelandet skickar dig att leta efter
en trasig sensor eller en glapp kabel, medan felet är att två enheter båda
tror att de är nummer 1.

**Adressregistret skiljer sig mellan givarna, och är OVERIFIERAT.** Detta
dokument, README och kodkommentarerna sa till 2026-08-10 att alla fem ändras
via `0x0200`. `sensor_databla.md` säger något annat, och det är den som har
databladen bakom sig:

| Sensor | Adressen ändras via |
|---|---|
| Syre | `0x2000` |
| Turbiditet | `0x2000` |
| Konduktivitet | `0x0200` |
| pH | `0x0200` |
| Nivå | `0x0000` |

Registren står nu per givare i `sensor_profiles.py` och skrivs ut av
`--factory`-läget. **Ingen har provat att skriva till dem** — kontrollera mot
databladets sida innan monteringsdagen.

**Återställningsknappen finns bara dokumenterad på konduktivitetsgivaren**
(SW1-SET, tre sekunder). Påståendet att alla fem har en gick inte att belägga
i databladen och är struket. Öva utan hårdvara:

```bash
python3 fake_sensors.py --factory
```

Simulatorn svarar då på fabriksadresserna, och en läsning mot adress 1 ger
`SVARAR INTE` plus en förklaring i loggen om varför.

| **KONTROLL** | Adress 55, 50 och 26 svarar. Adress 1 gör det inte, och simulatorn skriver ut `[KROCK]`. |
|---|---|

### 8.2 Byt transport

Du behöver en **USB-till-RS485-adapter**. Den finns inte i komponentlistan —
lägg till den i beställningen. Ett par hundralappar.

I `measure_cycle()` i `poller.py`:

```python
from pymodbus.client import ModbusSerialClient
client = ModbusSerialClient(port="/dev/ttyUSB0", baudrate=9600,
                            parity="N", stopbits=1, bytesize=8)
```

Inget annat i filen ändras. Modbus är Modbus.

### 8.3 Mät uppvärmningstiden

**Detta är etappens hela syfte.**

**Två** av fem tider står i databladen: konduktivitet och pH, båda under
2 sekunder. Nivåns 1 sekund är härledd ur en svarstid på 5 ms och är alltså
inte heller uppmätt, bara välgrundad. Kvar att verkligen mäta är **två**:
syresensorns antagna 40 sekunder och turbiditetens antagna 8.

Detta stod tidigare som "tre av fem står i databladen". Nivån räknades då som
angiven, vilket den inte är — databladet ger responstid, inte uppstartstid.
Rättat 2026-08-02. `WARMUP_SOURCE` i `fake_sensors.py` skriver nu ut källan
för varje tid när självtestet körs.

Sänk `warmup_s` stegvis — 90, 60, 45, 30, 20 — och notera när värdena börjar
avvika. Definiera ett stabilitetskriterium innan du tittar: till exempel att
värdet inte ändras mer än 0,05 mg/L över tio avläsningar.

Skriv in det uppmätta värdet i energibudgeten.

| Uppmätt | Last | Autonomi | Minsta panel |
|---|---|---|---|
| 30 s | 4,75 Wh | 55,0 dygn | 14,2 Wp |
| 45 s | 5,85 Wh | 44,6 dygn | 17,5 Wp |
| 60 s | 6,96 Wh | 37,5 dygn | 20,8 Wp |
| 90 s (antaget) | 9,16 Wh | 28,5 dygn | 27,4 Wp |
| 120 s | 11,37 Wh | 23,0 dygn | 33,9 Wp |

Visar det sig vara 45 sekunder kan panelen halveras och batteriet krympa.

### 8.3b Adaptiv uppvärmning — OVERIFIERAT, och därför avstängd

**Hypotesen:** en givare som inte hunnit sätta sig svarar med `0` i stället
för med ett felaktigt tal. Stämmer det behöver pollaren inte vänta en fast
tid — den kan läsa tills svaret blir giltigt och slå av strömmen i samma
sekund. Den största posten i energibudgeten skulle då gå från antagande till
mätning.

**Varifrån den kommer:** `sensor_databla.md` kapitel 4. Seeeds CO2-sensor
returnerar dokumenterat 0 före sin uppvärmningstid. **Ingenting säger att
våra fem gör det.** Det är en ledtråd om en annan produkt från samma
tillverkare, inget mer.

Flaggan `"adaptive_warmup"` finns i `config.json` och är **av**. Simulatorn
kan spela hypotesen med `"cold_returns_zero": true` i `faults.json`.

**Uppmätt i simulering 2026-08-10**, `warmup_s = 45`:

| Läge | Cykeltid | Syre | pH |
|---|---|---|---|
| Fast uppvärmning | 45,3 s | 10,43 | 8,47 |
| Adaptiv, hypotesen stämmer | 40,0 s | 10,65 | 8,48 |
| Adaptiv, hypotesen är **fel** | **0,5 s** | **5,73** | **5,09** |

Sista raden är hela risken, och den är värd att titta på en gång till. Den
ser ut som en energivinst på 99 procent. Den ger ett syrevärde 45 procent för
lågt och ett pH som hade utlöst larm — och den gör det **utan att något ser
trasigt ut**. Det är samma feltyp som `drift` och `stuck`: en givare som
svarar i tid med ett rimligt tal som råkar vara fel.

Att simulatorn kan spela hypotesen bevisar ingenting om hårdvaran. Den gör
bara det vi sagt åt den att göra.

| **KONTROLL** | Någon har strömsatt en givare och läst registret sekund för sekund under uppstart, och antecknat om det innehåller 0 eller ett osatt värde. **Inte gjort.** |
|---|---|

### 8.4 Två strömkanaler — det databladen avslöjade

**De två sensorer som drar mest ström är de som blir klara snabbast.**

Konduktivitet och pH är igång på under två sekunder enligt databladen. Syret
behöver fyrtio. Att mata alla fem i 90 sekunder för att en av dem kräver det
kostar ungefär **5 Wh per dygn av en budget på 12,8** — skillnaden mellan att
klara autonomikravet och att missa det.

Lösningen är två switchade strömkanaler i stället för en, så den snabba
gruppen kan läsas och stängas av medan syresensorn fortfarande sätter sig:

| Kanal | Sensorer | Uppvärmning |
|---|---|---|
| långsam | syre, turbiditet | 40 s respektive 8 s |
| snabb | konduktivitet, pH, nivå | 2 s, 2 s, 1 s |

**Mjukvaran är byggd sedan 2026-08-10. Hårdvaran är det inte.**
`"split_rail": true` i `config.json` får pollaren att slå på snabba kanalen,
vänta ut dess uppvärmning, läsa konduktivitet, pH och nivå, stänga av, och
sedan göra om det för syre och turbiditet. Avstängningen ligger i ett
`finally`, så den sker även när en avläsning kastar undantag. Kanaltillhörighet
och uppvärmningstid är per-givare-attribut i profilen.

Den andra MOSFET:en finns dock inte i bojen. Med en strömkanal mäter flaggan
bara samma givare i en krångligare ordning — värdet ligger i att koden och
siffran finns när kortet ritas om.

**Uppmätt i simulering 2026-08-10**, `warmup_s = 45`:

| Läge | Cykeltid | Kanaler |
|---|---|---|
| odelad | 45,1 s | fem givare strömbelagda hela tiden |
| delad | 42,0 s | snabb 2,0 s (tre givare), långsam 40,0 s (två) |

**Cykeltiden är fel storhet att döma delningen på.** Den krymper med tre
sekunder medan energin sjunker med runt tre fjärdedelar, eftersom den snabba
gruppen både är den törstiga och den som stängs av tidigt. Pollaren loggar
därför **wattsekunder per kanal**, inte bara varaktighet. Vid `warmup_s = 90`:

```
[rail]  odelad rail 92.0 s x 1.92-3.36 W = 177-309 Ws   (utan niva)
        delad hade kostat 29-33 Ws pa 44 s -- 89 procent mindre energi
        odelad med 40 s i stallet for 90 s hade kostat 81-141 Ws
        -- delningen tar resten
```

Tredje raden är den viktiga skillnaden: en del av vinsten kostar ingen
hårdvara alls, utan bara en kortare `warmup_s` — om profilens **gissade** 40 s
håller. Resten är vad den andra MOSFET:en faktiskt köper. Räknat på ett fast
2,85 W och 40 s blir motsvarande siffra 114 → 28 Ws, alltså 75 procent.

Spannet kommer av att konduktivitetens effekt bara är angiven vid 24 V.
Pollaren räknar och loggar detta **även när flaggan är av**, så beslutet kan
fattas utan att först köra ett dygn i delat läge.

Nivåsensorns strömförbrukning är **inte publicerad någonstans**. Den saknas i
energibudgeten, och alla siffror ovan är därför **undre gränser** — pollaren
skriver ut vilken givare som utelämnats.

| **KONTROLL** | Uppmätt uppvärmningstid för syre och turbiditet inskriven i Energibudget_vattenboj_v1.xlsx. Delad rail körd mot riktig hårdvara med två MOSFET:ar. Ingetdera gjort. |
|---|---|

---

## Efter etapp 8

Logiken portas till ESP32 i C. Strukturen är avsiktligt densamma:

| Python nu | ESP32 sedan |
|---|---|
| SQLite-buffert | SD-kort |
| `time.sleep()` | Djup sömn |
| Loggrad om strömpåslag | Riktig MOSFET-switch |
| `ModbusSerialClient` | RS485-transceiver på UART |

Pollaren är alltså inte firmware, men den är en fungerande specifikation av
vad firmwaren ska göra — i något du kan läsa.

Simulatorn kastas inte. Den behövs varje gång ni ändrar en larmregel, eftersom
alternativet är att framkalla syrebrist i en hink.

---

## Städa upp efter felsökningen ✅

Båda är borttagna 2026-07-28. Ingenting berodde på dem — `pip show pyyaml`
hade tomt `Required-by:`, och mosquitto behövdes inte eftersom etapp 0 löstes
med Docker i stället för väg B.

```bash
pip uninstall pyyaml        # gjort
brew uninstall mosquitto    # gjort
```

Notera att `mosquitto_sub` försvann med det. Behöver du lyssna på MQTT-trafik
för felsökning finns klienten kvar inuti containern:

```bash
docker exec limna-mosquitto mosquitto_sub -t 'limna/#' -v
```

---

## Kvar att göra som saknar test helt

**Krav K-04 — lokal lagring för minst 30 dygn.** Ingen etapp täcker det. En
räkning med den **nya nyttolasten på 199 byte**: 15 minuters intervall ger
2 880 mätningar på 30 dygn, alltså **560 KiB ren nyttolast**.

På disk är det mer, eftersom varje rad också bär id, tidsstämpel och
skickad-flagga plus SQLites sidhantering. Nu mätt i stället för gissat:
`buffer.db` med 1 031 rader väger 270 336 byte, alltså **262 byte per rad**.
För 30 dygn blir det **737 KiB**, knappt 0,75 MiB.

Kravet är därmed inte i närheten av att bli trångt på ett SD-kort ens med fem
sensorer.

> Siffran stod tidigare som 321 KiB och därefter 543 KiB. Den första byggde på
> 114 byte per mätning, vilket var självtestets förenklade exempel utan `namn`
> och `pos`. Den andra byggde på 193 byte, som fortfarande var för lågt.
> Overheaden angavs samtidigt som "cirka 1,6 MiB rejält tilltaget" — den
> uppmätta är 262 byte per rad, alltså mindre än hälften av det påslaget.

**Energibudgetens 120 byte stämmer inte.** Den siffran används för att räkna
mobildata och därmed 4G-modemets sändtid. Verkligt värde är 199 byte, alltså
**560 KiB per månad** i stället för antagna 338. Det behöver rättas i
`Energibudget_vattenboj_v1.xlsx`, inte i koden.

**`stuck` fångas av pollaren men syns inte i Grafana.** Kontrollen finns sedan
2026-08-10 och flaggar ett värde som varit bitidentiskt i `stuck_cycles`
mätningar i rad. Flaggan hamnar i `fel`-fältet, som telegraf släpper eftersom
det är text — se den kända begränsningen i etapp 6. Den syns alltså i loggen
och i `buffer.db`, men inte på dashboarden. För att ändra det krävs antingen
`json_string_fields` i `telegraf.conf` eller ett numeriskt fält i nyttolasten,
och det senare ändrar fältuppsättningen. **Ta det som ett eget beslut.**

Kontrollen är dessutom **inte provad över sex verkliga timmar** — bara med
nedsatt `stuck_cycles` mot simulatorns `stuck`-fel. Under den provningen
flaggades även en frisk konduktivitetsgivare, som stod still på samma heltal
i tre snabba cykler. Vid 24 cykler över sex timmar är det osannolikt, men
`ec` är den parameter som mest sannolikt ger falsklarm här.

**K-02 är förberett men inte påslaget, och serversidan är orörd.** Pollaren
kan sedan 2026-08-10 prata TLS och logga in, med lösenord ur miljövariabler,
men allt är avstängt som default. `mosquitto.conf`, `docker-compose.yml` och
`telegraf.conf` är **inte** ändrade — laptopuppsättningen är öppen med flit,
och att ändra det utan att fråga står under "Aldrig" i `CLAUDE.md`. Vad som
krävs för skarp drift står i README. Ingen del av det är gjord, och inget av
det är provat: TLS-koden har aldrig anslutit till en broker med certifikat.

**pH-elektroden behöver en kalibreringsrutin.** `drift` visar problemet men
löser det inte. En glaselektrod åldras och ska kalibreras med buffertlösning
med jämna mellanrum — hur ofta, och vem som gör det när bojen ligger ute i
Vombsjön, är en fråga för driftplanen och inte för koden.

**Fabriksadresseringen är inte provad mot hårdvara.** `--factory` simulerar
kollisionen, men själva skrivningen till adressregistret finns inte i koden —
den görs med ett Modbus-verktyg vid montering. Ingen har gjort det än, och
**vilket register det är skiljer sig mellan givarna** (`0x2000`, `0x0200`,
`0x0000`) och är OVERIFIERAT. Se etapp 8.1b.

**Larmgränserna i bojen är PLACEHOLDER, och de är inte våra att sätta.**
`alarm_limits` i `config.json` styr sedan 2026-08-10 vilka mätningar som
skickas direkt (F-12). Tre av talen är beslutade och hämtade ur
larmreglerna — `do` 5,0, `batt` 12,4, `temp_d` 1,0. Resten är platshållare
märkta som sådana: `ph` 6,0–9,5, `turb` 100 NTU, `temp` 25 °C, `do_d`
1,0 mg/L.

**Krav Ö-08:** larmnivåer per parameter fastställs av Swedamm tillsammans med
kommunen, för den faktiska pilotplatsen. Platshållarna finns för att
mekaniken ska gå att prova, inte för att de är rätt.

`ec` och `lvl` har medvetet **inga** gränser. Konduktiviteten stod först på
1000 µS/cm mot ett driftområde på 50–300 — den hade aldrig löst ut, och en
gräns som aldrig löser ut är sämre än ingen alls eftersom den ser ut som ett
skydd. Nivåns gränser beror på var bojen förankras.

**`do_d` har ingen larmregel i Grafana.** Bojen känner igen gränsen själv,
men de sex reglerna letar inte efter påväxtdrift. Det behövs först när
fälttestet med två syregivare körs (U-04).