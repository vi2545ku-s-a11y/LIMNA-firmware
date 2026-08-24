# LIMNA — kontext för Claude Code

## Vad projektet är

LIMNA är en solcellsdriven mätboj som ligger förankrad i en svensk sjö eller
ett dagvattenmagasin och mäter vattenkvalitet dygnet runt. Den läser fem
RS485/Modbus-sensorer — sex under fälttest, se U-04 — buffrar lokalt och
skickar över 4G till en dashboard. Kunden är en kommunal handläggare, inte en
tekniker.

Utvecklas av HydroVault tillsammans med Swedamm. Första piloten planeras i
Vombsjön, Sjöbo kommun.

Koden i denna mapp är en **laptop-repetition** av firmwaren. Sensorerna
simuleras så att hela kedjan kan byggas innan hårdvaran har levererats.

## Begränsningar som väger tyngre än vanlig kodkvalitet

**Pollerns struktur speglar avsiktligt firmware som ska köra på en ESP32.**
Slå inte ihop mätning och sändning. Ersätt inte SQLite-bufferten med något
elegantare. Gör inte om den blockerande loopen till async. Varje sådan
"förbättring" gör porteringen svårare. SQLite blir SD-kort, `time.sleep`
blir djup sömn, loggraden om strömpåslag blir en riktig MOSFET-switch.

**Registerkartan gäller en specifik givarfamilj, inte projektet.** Adresser
och skalfaktorer är verifierade mot databladen (2026-07-29) för S-RJY-01,
S-DTS210-01, S-EC-01, S-pH-01A och S-YW-01B. Den familjen är **ännu inte
upphandlad**. Slutligt val kan bli en annan, till exempel B&C Electronics
8325, och då stämmer inte en enda adress eller skalfaktor i den nuvarande
kartan.

Därför bor kartan på **ett** ställe, som en utbytbar profil. Den stod en gång
på två — `REGISTERS` i `fake_sensors.py` och `SENSORS` i `poller.py` — utan
någon kontroll av att de sade samma sak, och ändrades den ena följde den andra
inte med. Sedan 2026-08-09 är båda vyer av samma tabell, och
`verify_consistency()` stannar om de skulle glida isär. Byte av givarfamilj är
att lägga till en profil och peka `config.json` på den.

Sedan 2026-08-24 ligger filen i `delad/sensor_profiles.py`, medvetet utanför
`laptop/`, eftersom ESP32-firmwaren ska läsa exakt samma fil och kopiera den
till kortet vid flashning. Laptopprogrammen når den med en sökvägsrad överst i
varje fil. **Lägg aldrig en kopia i `laptop/` eller `firmware/`** — det är
precis det felet profilen finns för att förhindra.

Vad som överlever ett familjebyte och vad som inte gör det:

- **Portabelt:** driftadresserna 1–5 (6 under fälttest). De sätter vi själva
  vid montering och de är oberoende av fabrikat.
- **Produktspecifikt:** fabriksadresser, register, skalfaktorer, signerad
  eller osignerad, uppvärmningstider med källa, och ledarfärger. Allt detta
  byts ut med familjen.

Innehållet ska fortfarande inte "städas" och inte ändras utan ett datablad i
handen. Att kartan ser inkonsekvent ut är sant, inte slarvigt: turbiditeten
skalar med 10 medan syret skalar med 100, konduktiviteten med 1, och tre
register är signerade int16 medan resten är osignerade. Varje tillverkare gör
olika.

**Uppvärmningstiderna är en annan sak.** Två av fem står i databladen
(konduktivitet och pH). Nivåns är **härledd** ur en angiven svarstid på 5 ms,
vilket inte är samma sak som uppstartstid. Syrets 40 sekunder och
turbiditetens 8 är gissningar. Allt detta står i `WARMUP_SOURCE` och skrivs
ut av självtestet — ändras tiderna måste källan följa med. Nivåsensorns
strömförbrukning är inte publicerad någonstans och saknas därför i
energibudgeten.

**Koden ska gå att läsa av någon utan djup programmeringsvana.** Projektägaren
är konstruktör, inte utvecklare. Föredra tydlighet framför korthet. Undvik
ramverk, abstraktionslager och beroenden som inte behövs.

**Språk:** kommentarer i koden på engelska, all användarvänd text och
dokumentation på svenska. README.md är svensk och ska förbli det.

## Det tal som styr allt

Uppvärmningstiden för sensorerna (`warmup_s`) är antagen till 90 sekunder och
står för **58,8 procent av lasten före säkerhetsmarginalen**, vilket
motsvarar samma andel av totalen när marginalen fördelas proportionellt. Den
styr i sin tur solpanelens och batteriets storlek. Den tidigare siffran
72 procent kom ur en äldre version av budgeten och gäller inte.

Räkningen, eftersom två nämnare cirkulerar: sensorrailen drar 6,84 Wh/dygn av
en delsumma på 11,64 Wh före marginal, alltså 58,8 procent. Marginalen på
15 procent är inte en egen last utan ett proportionellt påslag för
omodellerade förluster i allt. Fördelas den tillbaka blir railen
6,84 × 1,15 = 7,87 Wh av totalen 13,39 — återigen 58,8 procent. Att i stället
dividera 6,84 med 13,39 ger 51 procent och jämför en post *utan* påslag med en
summa *med* påslag. Andelskolumnen i Beräkningsunderlaget summerar till
100 procent mot delsumman.

**I november är energibalansen i praktiken noll** med odelad rail och 90
sekunders uppvärmning. Produktionen räcker precis till förbrukningen och det
finns ingen marginal för en mulen vecka. Därför är mätningen av den verkliga
uppvärmningstiden projektets **enskilt viktigaste mätning**, och delad
sensorrail (E-11, E-12) den enskilt viktigaste hårdvaruändringen.

Därför är uppvärmningssimuleringen i `fake_sensors.py` inte en detalj utan
själva poängen: den ska visa att en sensor som läses för tidigt efter
strömpåslag ger fel värde. Om något i den koden ändras måste beteendet
kvarstå och verifieras.

## Kravbeteckningar som förekommer i README och kommentarer

De kommer från projektets kravspecifikation, som inte ligger i denna mapp.

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
| U-04 | Två syregivare parallellt under fälttest, för att mäta påväxtdrift |
| Ö-08 | Larmnivåer per parameter fastställs av Swedamm tillsammans med kommunen, för den faktiska pilotplatsen |
| D-01 till D-09 | Krav på dashboarden: karta, grafer, status, larmhistorik, export |

## Korsvis kontroll som designmönster

En givare som slutar svara är lätt att upptäcka. En givare som fortsätter
svara i tid, med ett rimligt men felaktigt tal, är det inte. Det är den enda
feltyp som varken gränsvärdeslarm (F-10), taktlarm (F-11) eller tystnadslarm
(F-13) fångar. En sådan sensor slutar inte svara — den börjar ljuga.

Tre saker i koden löser samma problem på samma sätt: de ger värdet något att
jämföras med.

| Kontroll | Jämför | Fångar |
|---|---|---|
| `temp_d` | fyra termometrar i samma vatten | en givare vars kort driver |
| `do_d` | två syregivare parallellt (U-04) | biologisk påväxt på den ena |
| fastfruset värde | ett värde mot sin egen historik | en givare som frusit fast |

Kalibreringsfel ger ungefär 0,2 °C spridning mellan de fyra termometrarna;
över 1,0 °C driver en av dem. Påväxtdriften är hela syftet med U-04 —
skillnaden mellan de två syregivarna är måttet, inte något av värdena var för
sig. Och ett värde som är bitidentiskt över många cykler har ingenting annat
än sin egen historik att jämföras med.

Mönstret är billigt: några byte i nyttolasten i stället för en servicerunda
till Vombsjön. Ta inte bort dessa fält för att spara plats.

## Sådant som ser ut som buggar men inte är det

- Mätvärden skrivs till disk **innan** något försök görs att skicka dem. Det
  är avsiktligt och är krav K-03. Motsatt ordning tappar data varje gång
  täckningen viker, vilket vid en svensk sjö sker ofta.
- Tidsstämpeln sätts i bojen, inte på servern. En mätning som legat i
  bufferten i tre dygn ska hamna på rätt tidpunkt i grafen.
- Pollaren sover mellan mätningarna i stället för att schemalägga. Det speglar
  djup sömn på mikrokontrollern.

## Aldrig

- Committa `buffer.db`, `config.json` eller `faults.json`.
- Lägga till autentisering eller TLS i utvecklingsuppsättningen utan att
  fråga. Att den är öppen är ett medvetet val för laptopbruk, dokumenterat
  sist i README.
- Ändra nyttolastens fältnamn (`do`, `turb`, `ec`, `ph`, `lvl`, `temp`,
  `temp_d`, `batt`) utan att fråga. De är korta för att spara mobildata och
  används av telegraf, dashboarden och energibudgeten. `ph` och `temp_d`
  tillkom 2026-07-29.

  **Undantag:** `do2` och `do_d` får läggas till enligt U-04 — den andra
  syregivaren och skillnaden mellan de två. Övriga fältnamn ligger fast.

  Nyttolasten är **199 byte** och den siffran är korrekt. Uppmätt spann
  197–201; storleken varierar med hur många decimaler varje mätvärde hamnar
  på. `ec` går alltid ut som float (`"ec":318.0`), eftersom
  `decode_register()` avrundar. Energibudgetens antagna **120 byte är fel**
  och stämde inte med fyra sensorer heller (176 byte). Det ska rättas i
  Beräkningsunderlaget, inte i koden.

  `temp_d` är inte en mätstorhet utan en **kontroll**: största skillnaden
  mellan de fyra temperaturgivarna. Se avsnittet om korsvis kontroll ovan.
  Ta inte bort fältet för att spara nio byte.


